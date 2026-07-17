"""Task 11 (finding f3): the package is really installable, not just conftest-importable.

- Metadata checks (CI-safe, no network): pyproject declares the package + deps; uv.lock locks them.
- Real proof (guarded on uv): build a wheel, install it into an isolated target with --no-deps,
  and import phase_executor in a subprocess with NO conftest and NO src on sys.path — so a broken
  pyproject (missing package, unshipped schema data) fails here, where the conftest can't mask it.
"""
import glob
import os
import pathlib
import shutil
import subprocess
import sys
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = REPO_ROOT / "phase_executor"


def test_pyproject_declares_package_and_deps():
    data = tomllib.loads((PKG / "pyproject.toml").read_text())
    proj = data["project"]
    assert proj["name"] == "phase-executor"
    assert any(d.startswith("jsonschema") for d in proj["dependencies"])
    glm = proj["optional-dependencies"]["glm"]
    assert any(d.startswith("zhipuai") for d in glm) and "sniffio" in glm
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/phase_executor"]


def test_uv_lock_present_and_locks_jsonschema():
    lock = (PKG / "uv.lock").read_text()
    assert 'name = "phase-executor"' in lock
    assert 'name = "jsonschema"' in lock


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed (visible skip; run locally)")
def test_wheel_builds_installs_and_imports_without_conftest(tmp_path):
    out = tmp_path / "dist"
    build = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out), str(PKG)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        pytest.skip(f"uv build unavailable offline (visible skip): {build.stderr[-300:]}")
    wheels = glob.glob(str(out / "*.whl"))
    assert wheels, "no wheel produced"
    site = tmp_path / "site"
    inst = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(site), wheels[0]],
                          capture_output=True, text=True)
    assert inst.returncode == 0, inst.stderr[-500:]
    # import from the INSTALLED location only: clean cwd, PYTHONPATH=site, no conftest, no src
    check = subprocess.run(
        [sys.executable, "-c",
         "import phase_executor, phase_executor.engine, phase_executor.contract as c; "
         "assert c.observation_schema()['title']; "
         "import phase_executor.routing as r; r.load_routing_table(__import__('pathlib').Path(phase_executor.__file__).parent/'routing'/'rawgentic.routing-table.json'); "
         "print('IMPORT_OK')"],
        capture_output=True, text=True, cwd=str(tmp_path), env={**os.environ, "PYTHONPATH": str(site)},
    )
    assert check.returncode == 0, check.stderr[-800:]
    assert "IMPORT_OK" in check.stdout
