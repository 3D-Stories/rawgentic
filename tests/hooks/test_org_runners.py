"""Tests for hooks/org_runners_lib.py — the tested core of the
`rawgentic:admit-to-org-runners` skill (#397).

Covers the risky logic the skill must never get wrong:
  - classifying a workflow `runs-on:` (hosted / already-fleet / expression / list),
  - OS -> required-label mapping,
  - matching required labels against an ONLINE runner (case-insensitive subset),
  - planning a migration fail-closed (any un-migratable hosted lane refuses the
    whole file — never a partial migration that leaves a hosted fallback),
  - rewriting a hosted scalar into a `{group, labels}` block without touching
    anything else,
  - detecting a hosted remnant (idempotency + post-migration verification).
"""
import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import org_runners_lib as orl  # noqa: E402

CLI = HOOKS_DIR / "org_runners_lib.py"

# Real fleet shape (3ds-fleet) — online runners carry a superset of the minimal
# [self-hosted, <os>] labels the migration targets.
FLEET_RUNNERS = [
    {"name": "3ds-fleet-linux", "status": "online",
     "labels": ["self-hosted", "Linux", "X64", "saystory"]},
    {"name": "3ds-fleet-windows", "status": "online",
     "labels": ["self-hosted", "X64", "saystory", "Windows"]},
    {"name": "3ds-fleet-mac", "status": "offline",
     "labels": ["self-hosted", "macOS", "ARM64", "fleet"]},
]

HOSTED_CI = """\
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: cargo test
"""

FLEET_CI = """\
name: ci
on: [push]
jobs:
  test:
    runs-on:
      group: 3ds-fleet
      labels: [self-hosted, linux]
    steps:
      - run: cargo test
"""

MATRIX_CI = """\
name: ci
on: [push]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - run: cargo test
"""

SELFHOSTED_LIST_CI = """\
name: ci
on: [push]
jobs:
  test:
    runs-on: [self-hosted, linux]
    steps:
      - run: cargo test
"""


# ---------- hosted_os ----------

def test_hosted_os_maps_github_labels():
    assert orl.hosted_os("ubuntu-latest") == "linux"
    assert orl.hosted_os("ubuntu-22.04") == "linux"
    assert orl.hosted_os("windows-latest") == "windows"
    assert orl.hosted_os("windows-2022") == "windows"
    assert orl.hosted_os("macos-latest") == "macos"
    assert orl.hosted_os("macos-14") == "macos"


def test_hosted_os_case_insensitive():
    assert orl.hosted_os("Ubuntu-Latest") == "linux"


def test_hosted_os_none_for_self_hosted_or_custom():
    assert orl.hosted_os("self-hosted") is None
    assert orl.hosted_os("my-custom-runner") is None
    assert orl.hosted_os("linux") is None  # bare 'linux' is a self-hosted label, not hosted


# ---------- classify_runs_on ----------

def test_classify_hosted():
    assert orl.classify_runs_on("ubuntu-latest") == "hosted"


def test_classify_expression():
    assert orl.classify_runs_on("${{ matrix.os }}") == "expression"


def test_classify_selfhosted_list():
    assert orl.classify_runs_on("[self-hosted, linux]") == "fleet"


def test_classify_hosted_inside_list_is_manual():
    # A list that names a hosted label can't be safely 1:1 mapped -> manual, never mangled.
    assert orl.classify_runs_on("[ubuntu-latest]") == "manual"


# ---------- labels_satisfied_by ----------

def test_labels_subset_case_insensitive():
    assert orl.labels_satisfied_by(
        ["self-hosted", "linux"], ["self-hosted", "Linux", "X64", "saystory"])


def test_labels_not_satisfied_when_missing():
    assert not orl.labels_satisfied_by(
        ["self-hosted", "windows"], ["self-hosted", "Linux", "X64"])


# ---------- online_runner_for ----------

def test_online_runner_found_for_linux():
    r = orl.online_runner_for(["self-hosted", "linux"], FLEET_RUNNERS)
    assert r and r["name"] == "3ds-fleet-linux"


def test_offline_runner_never_selected():
    # mac runner satisfies the labels but is OFFLINE -> migration must not target it.
    assert orl.online_runner_for(["self-hosted", "macos"], FLEET_RUNNERS) is None


# ---------- find_runs_on ----------

def test_find_runs_on_hosted_scalar():
    occ = orl.find_runs_on(HOSTED_CI)
    assert len(occ) == 1
    assert occ[0]["kind"] == "hosted"
    assert occ[0]["os"] == "linux"


def test_find_runs_on_fleet_block():
    occ = orl.find_runs_on(FLEET_CI)
    assert len(occ) == 1
    assert occ[0]["kind"] == "fleet"


# ---------- has_hosted_remnant / is_migrated ----------

def test_hosted_ci_has_remnant():
    assert orl.has_hosted_remnant(HOSTED_CI)
    assert not orl.is_migrated(HOSTED_CI)


def test_fleet_ci_has_no_remnant():
    assert not orl.has_hosted_remnant(FLEET_CI)
    assert orl.is_migrated(FLEET_CI)


# ---------- plan_migration ----------

def test_plan_hosted_ready():
    plan = orl.plan_migration(HOSTED_CI, "3ds-fleet", FLEET_RUNNERS)
    assert plan["verdict"] == "ready"
    assert plan["jobs"][0]["action"] == "migrate"
    assert plan["jobs"][0]["target_labels"] == ["self-hosted", "linux"]


def test_plan_already_fleet_is_noop():
    plan = orl.plan_migration(FLEET_CI, "3ds-fleet", FLEET_RUNNERS)
    assert plan["verdict"] == "noop"
    assert plan["jobs"][0]["action"] == "skip"


def test_plan_blocked_when_no_online_runner():
    # macos hosted job but the only macos runner is offline -> STOP, never strand CI.
    macos_ci = HOSTED_CI.replace("ubuntu-latest", "macos-latest")
    plan = orl.plan_migration(macos_ci, "3ds-fleet", FLEET_RUNNERS)
    assert plan["verdict"] == "blocked"
    assert plan["jobs"][0]["action"] == "blocked"


def test_plan_expression_is_manual_and_fails_closed():
    plan = orl.plan_migration(MATRIX_CI, "3ds-fleet", FLEET_RUNNERS)
    assert plan["verdict"] == "manual"
    assert plan["jobs"][0]["action"] == "manual"


def test_plan_selfhosted_list_is_noop():
    plan = orl.plan_migration(SELFHOSTED_LIST_CI, "3ds-fleet", FLEET_RUNNERS)
    assert plan["verdict"] == "noop"


# ---------- rewrite_migration ----------

def test_rewrite_hosted_scalar_to_block():
    plan = orl.plan_migration(HOSTED_CI, "3ds-fleet", FLEET_RUNNERS)
    out = orl.rewrite_migration(HOSTED_CI, plan, "3ds-fleet")
    assert "runs-on:\n      group: 3ds-fleet\n      labels: [self-hosted, linux]" in out
    # nothing else touched
    assert "name: ci" in out and "- run: cargo test" in out
    # result is fully migrated (idempotent target)
    assert orl.is_migrated(out)


def test_rewrite_refuses_when_not_ready():
    plan = orl.plan_migration(MATRIX_CI, "3ds-fleet", FLEET_RUNNERS)
    try:
        orl.rewrite_migration(MATRIX_CI, plan, "3ds-fleet")
        assert False, "expected refusal on a non-ready plan"
    except ValueError:
        pass


def test_rewrite_of_noop_is_identity():
    plan = orl.plan_migration(FLEET_CI, "3ds-fleet", FLEET_RUNNERS)
    assert orl.rewrite_migration(FLEET_CI, plan, "3ds-fleet") == FLEET_CI


# ---------- CLI ----------

def _run(args, stdin=None):
    return subprocess.run(
        [sys.executable, str(CLI), *args], input=stdin,
        capture_output=True, text=True)


def test_cli_plan_ready_exit0(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text(HOSTED_CI)
    r = _run(["plan", "--workflow", str(wf), "--group", "3ds-fleet",
              "--runners", "-"], stdin=json.dumps(FLEET_RUNNERS))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["verdict"] == "ready"


def test_cli_plan_blocked_exit1(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text(HOSTED_CI.replace("ubuntu-latest", "macos-latest"))
    r = _run(["plan", "--workflow", str(wf), "--group", "3ds-fleet",
              "--runners", "-"], stdin=json.dumps(FLEET_RUNNERS))
    assert r.returncode == 1
    assert json.loads(r.stdout)["verdict"] == "blocked"


def test_cli_check_hosted_detects_remnant(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text(HOSTED_CI)
    assert _run(["check-hosted", "--workflow", str(wf)]).returncode == 1
    wf.write_text(FLEET_CI)
    assert _run(["check-hosted", "--workflow", str(wf)]).returncode == 0


def test_cli_rewrite_in_place(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text(HOSTED_CI)
    r = _run(["rewrite", "--workflow", str(wf), "--group", "3ds-fleet",
              "--runners", "-", "--in-place"], stdin=json.dumps(FLEET_RUNNERS))
    assert r.returncode == 0, r.stderr
    assert orl.is_migrated(wf.read_text())


# ---------- review hardening (#397 diff review): unusual-but-valid YAML shapes ----------
# A hosted lane in any of these spec-valid shapes must NEVER read as clean/noop —
# that is the silent-CI-death the module exists to prevent.

SPACE_COLON_CI = HOSTED_CI.replace("runs-on: ubuntu-latest", "runs-on : ubuntu-latest")
QUOTED_KEY_CI = HOSTED_CI.replace("runs-on: ubuntu-latest", '"runs-on": ubuntu-latest')
QUOTED_VAL_CI = HOSTED_CI.replace("runs-on: ubuntu-latest", 'runs-on: "ubuntu-latest"')


def test_hosted_os_strips_quotes():
    assert orl.hosted_os('"ubuntu-latest"') == "linux"
    assert orl.hosted_os("'macos-14'") == "macos"


def test_space_before_colon_detected_as_hosted():
    occ = orl.find_runs_on(SPACE_COLON_CI)
    assert len(occ) == 1 and occ[0]["kind"] == "hosted" and occ[0]["os"] == "linux"
    assert orl.has_hosted_remnant(SPACE_COLON_CI)
    assert orl.plan_migration(SPACE_COLON_CI, "g", FLEET_RUNNERS)["verdict"] == "ready"


def test_quoted_key_detected_as_hosted():
    assert orl.has_hosted_remnant(QUOTED_KEY_CI)
    assert orl.plan_migration(QUOTED_KEY_CI, "g", FLEET_RUNNERS)["verdict"] == "ready"


def test_quoted_value_is_hosted_not_clean():
    # the safety primitive (check-hosted / is_migrated) must not call a
    # genuinely-hosted file clean, and it should be migratable.
    assert orl.has_hosted_remnant(QUOTED_VAL_CI)
    assert not orl.is_migrated(QUOTED_VAL_CI)
    assert orl.plan_migration(QUOTED_VAL_CI, "g", FLEET_RUNNERS)["verdict"] == "ready"


def test_rewrite_runs_on_as_last_line_no_newline():
    text = "jobs:\n  t:\n    runs-on: ubuntu-latest"  # no trailing newline
    plan = orl.plan_migration(text, "3ds-fleet", FLEET_RUNNERS)
    out = orl.rewrite_migration(text, plan, "3ds-fleet")
    assert orl.is_migrated(out)
    # the block must be three separate lines, never collapsed onto one
    assert "runs-on:\n      group: 3ds-fleet\n      labels: [self-hosted, linux]" in out
    assert "runs-on:      group" not in out
