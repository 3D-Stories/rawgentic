"""Tests for hooks/campaign-merge-guard.py — PreToolUse guard for campaign merges (#976).

The guard refuses a raw `gh pr merge` whose PR belongs to an ACTIVE campaign, and points
the caller at `broker-merge` instead. Covers the issue's five acceptance criteria:

- AC1 refusal decided from durable driver state, never session context
- AC2 the broker's own merge is not blocked, with no spoofable signal
- AC3 the fail mode, settled per path (open before classification, closed after)
- AC4 non-campaign merges byte-for-byte unaffected
- AC5 a legible denial

Hooks are tested black-box via subprocess with JSON on stdin exactly as Claude Code
invokes them (docs/testing.md:5-8); the pure decision functions are imported directly.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.hooks.conftest import parse_hook_output, run_hook

HOOK = "campaign-merge-guard.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import campaign_merge_guard_lib as lib  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────

CONFIGURED_REPO = "3D-Stories/rawgentic"


def _project(tmp_path: Path, *, repo: str = CONFIGURED_REPO) -> Path:
    """A project root with a .rawgentic.json, like every real project here."""
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".rawgentic.json").write_text(json.dumps({
        "version": 1,
        "project": {"name": "rawgentic", "type": "library"},
        "repo": {"provider": "github", "fullName": repo, "defaultBranch": "main"},
    }), encoding="utf-8")
    return root


def _campaign(root: Path, name: str, issues: list[dict]) -> Path:
    """Write a driver-state file in the REAL shape.

    Real driver state carries top-level `issues: [{"number": N, "status": …, "pr": M}]`
    — verified against claude_docs/.driver-state/epic-875-stay-small.json and every
    reader in hooks/driver_lib.py. A fixture that invents a different shape is the
    exact bug this file exists to prevent recurring (see the T0 regression in
    tests/hooks/test_launcher_lib.py).
    """
    state_dir = root / "claude_docs" / ".driver-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{name}.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "campaign": name,
        "project": "rawgentic",
        "epic": 875,
        "epic_status": "open",
        "issues": issues,
    }), encoding="utf-8")
    return path


def _guard(command: str, cwd: Path) -> tuple[str, str, int]:
    return run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": command}},
                    cwd=cwd)


def _decision(command: str, cwd: Path) -> str:
    stdout, _stderr, _rc = _guard(command, cwd)
    parsed = parse_hook_output(stdout)
    if parsed is None:
        return "allow"
    got = parsed.get("hookSpecificOutput", {}).get("permissionDecision", "")
    return "deny" if got == "deny" else "allow"


# ── AC1: refuse a raw merge whose PR belongs to an active campaign ────────

def test_ac1_denies_raw_merge_of_an_active_campaign_pr(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "epic-875-stay-small",
              [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision("gh pr merge 887 --squash --delete-branch", root) == "deny"


def test_ac1_decided_from_durable_state_not_session_context(tmp_path):
    """No environment variable and no session field can turn the refusal on or off."""
    root = _project(tmp_path)
    _campaign(root, "epic-875-stay-small",
              [{"number": 880, "status": "in_progress", "pr": 887}])
    stdout, _stderr, _rc = run_hook(
        HOOK,
        {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 887"},
         "session_id": "whatever", "campaign": "none", "cwd": str(root)},
        cwd=root,
        env_override={"RAWGENTIC_CAMPAIGN": "", "CLAUDE_CODE_SESSION_ID": "x"},
    )
    parsed = parse_hook_output(stdout)
    assert parsed is not None
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("status", ["queued", "in_progress", "pr_open"])
def test_ac1_active_statuses_deny(tmp_path, status):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": status, "pr": 887}])
    assert _decision("gh pr merge 887", root) == "deny"


@pytest.mark.parametrize("status", ["merged", "deferred", "abandoned"])
def test_ac1_disposed_statuses_allow(tmp_path, status):
    """A campaign whose every child is disposed is finished — it gates nothing."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": status, "pr": 887}])
    assert _decision("gh pr merge 887", root) == "allow"


def test_ac1_unparseable_pr_number_with_an_active_campaign_denies(tmp_path):
    """`gh pr merge` with no number targets the current branch's PR — unknowable here."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision("gh pr merge --squash", root) == "deny"


# ── AC2: the broker's own merge is never blocked, with no spoofable signal ─

def test_ac2_the_broker_command_is_not_classified_as_a_raw_merge():
    """`broker-merge` carries no `gh pr merge` token sequence, so the pre-filter misses it."""
    assert lib.parse_merge_command(
        "python3 hooks/launcher_lib.py broker-merge --pr 887 --issue 880 "
        "--campaign epic-875-stay-small --project-root ."
    ) is None


def test_ac2_the_broker_command_is_allowed_with_a_campaign_active(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "epic-875-stay-small",
              [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision(
        "python3 hooks/launcher_lib.py broker-merge --pr 887 --issue 880 "
        "--campaign epic-875-stay-small --project-root .", root) == "allow"


def test_ac2_the_broker_spawns_gh_with_a_list_argv_not_a_shell_string():
    """The signal is a PROCESS BOUNDARY, not a token — so pin the shape it rests on.

    PreToolUse fires per Claude Code tool call, not per OS process. The broker's merge is
    `subprocess.run([...], shell=False)` from Python, so it is not a Bash tool call and
    this hook never sees it. If that ever became a shell string routed through the Bash
    tool, the guard would start blocking the broker itself.
    """
    source = (REPO_ROOT / "hooks" / "launcher_lib.py").read_text(encoding="utf-8")
    assert ('argv = ["gh", "pr", "merge", str(pr), "--repo", repo, "--squash", '
            '"--delete-branch"]') in source
    assert "shell=False" in source


# ── AC3: the fail mode, settled per path ──────────────────────────────────

def test_ac3_unparseable_stdin_fails_open_and_says_so():
    """Before classification: ALLOW. A bug here would otherwise block every Bash call."""
    hook = REPO_ROOT / "hooks" / HOOK
    proc = subprocess.run([sys.executable, str(hook)], input="not json at all",
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert parse_hook_output(proc.stdout) is None
    assert proc.stderr.strip(), "the fail-open path must not be silent (finding F4)"


def test_ac3_missing_command_field_fails_open(tmp_path):
    root = _project(tmp_path)
    stdout, _stderr, rc = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {}},
                                   cwd=root)
    assert rc == 0
    assert parse_hook_output(stdout) is None


def test_ac3_corrupt_campaign_state_fails_closed(tmp_path):
    """After classification: DENY. The blast radius is one refused raw command."""
    root = _project(tmp_path)
    state_dir = root / "claude_docs" / ".driver-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "broken.json").write_text("{ this is not json", encoding="utf-8")
    assert _decision("gh pr merge 887", root) == "deny"


def test_ac3_corrupt_state_does_not_block_a_non_merge_command(tmp_path):
    """The fail-CLOSED path is reachable ONLY after the command is classified."""
    root = _project(tmp_path)
    state_dir = root / "claude_docs" / ".driver-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "broken.json").write_text("{ this is not json", encoding="utf-8")
    assert _decision("pytest tests/ -q", root) == "allow"
    assert _decision("ls -la", root) == "allow"
    assert _decision("git commit -m 'wip'", root) == "allow"


def test_ac3_missing_state_directory_is_absence_not_failure(tmp_path):
    """ENOENT under a valid root is absence — the rule docs/supervision.md already states."""
    root = _project(tmp_path)
    assert not (root / "claude_docs" / ".driver-state").exists()
    assert _decision("gh pr merge 887", root) == "allow"


def test_ac3_no_project_root_allows(tmp_path):
    """No .rawgentic.json above cwd: there is no campaign to gate on."""
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _decision("gh pr merge 887", bare) == "allow"


def test_ac3_oversized_state_file_fails_closed(tmp_path):
    root = _project(tmp_path)
    state_dir = root / "claude_docs" / ".driver-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "huge.json").write_text(
        "[" + ("0," * (lib.MAX_STATE_BYTES // 2)) + "0]", encoding="utf-8")
    assert _decision("gh pr merge 887", root) == "deny"


# ── AC4: non-campaign merges are byte-for-byte unaffected ─────────────────

def test_ac4_no_campaign_file_at_all_allows(tmp_path):
    root = _project(tmp_path)
    assert _decision("gh pr merge 887 --squash --delete-branch", root) == "allow"


def test_ac4_a_campaign_that_does_not_name_this_pr_allows(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision("gh pr merge 999", root) == "allow"


def test_ac4_a_foreign_repo_target_allows(tmp_path):
    """Finding F3: PR numbers are repo-scoped, so a foreign --repo must not collide."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision("gh pr merge 887 --repo other-org/other-repo", root) == "allow"


def test_ac4_this_repo_named_explicitly_still_denies(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision(f"gh pr merge 887 --repo {CONFIGURED_REPO}", root) == "deny"


@pytest.mark.parametrize("command", [
    "gh pr create --title x --body y",
    "gh pr view 887 --json state",
    "gh pr list --state open",
    "gh issue view 976",
    "git merge main",
    "echo 'run gh pr merge 887 by hand'",
    'echo "gh pr merge 887"',
    "grep -rn 'gh pr merge' docs/",
    "git commit -m 'document gh pr merge 887'",
])
def test_ac4_non_merge_commands_allow(tmp_path, command):
    """A MENTION of the command is not an INVOCATION of it."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision(command, root) == "allow"


@pytest.mark.parametrize("command", [
    "gh pr merge 887",
    "  gh pr merge 887",
    "cd . && gh pr merge 887 --squash",
    "git fetch origin; gh pr merge 887",
    "GH_TOKEN=x gh pr merge 887",
])
def test_ac1_command_position_invocations_deny(tmp_path, command):
    """Real invocations, including after a separator or an env-var prefix.

    A `cd` to somewhere OUTSIDE the project is deliberately NOT here: after the Step 8a
    F3 fix that merge really would run elsewhere, so allowing it is correct. The
    cd-into-a-campaign case has its own test below.
    """
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision(command, root) == "deny"


# ── AC5: a legible denial ─────────────────────────────────────────────────

def test_ac5_denial_names_campaign_issue_pr_reason_and_the_broker_command(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "epic-875-stay-small",
              [{"number": 880, "status": "pr_open", "pr": 887}])
    stdout, _stderr, _rc = _guard("gh pr merge 887", root)
    parsed = parse_hook_output(stdout)
    assert parsed is not None
    reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]

    assert "epic-875-stay-small" in reason, "names the campaign"
    assert "880" in reason, "names the issue"
    assert "887" in reason, "names the PR"
    assert "broker-merge" in reason, "names the command to run instead"
    assert "--campaign epic-875-stay-small" in reason
    assert "--issue 880" in reason
    assert "--pr 887" in reason
    assert len(reason.splitlines()) > 2, "never a bare one-line 'blocked'"


def test_ac5_the_unevaluable_denial_says_why(tmp_path):
    root = _project(tmp_path)
    state_dir = root / "claude_docs" / ".driver-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "broken.json").write_text("{ nope", encoding="utf-8")
    stdout, _stderr, _rc = _guard("gh pr merge 887", root)
    reason = parse_hook_output(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "broken.json" in reason, "names the file it could not read"
    assert "broker-merge" in reason


# ── the pure decision layer ───────────────────────────────────────────────

@pytest.mark.parametrize("command,pr,repo", [
    ("gh pr merge 887", 887, None),
    ("gh pr merge 887 --squash --delete-branch", 887, None),
    ("gh pr merge --squash 887", 887, None),
    ("gh pr merge 887 --repo a/b", 887, "a/b"),
    ("gh pr merge --repo a/b 887", 887, "a/b"),
    ("gh  pr   merge  887", 887, None),
    ("gh pr merge", None, None),
    ("gh pr merge --auto", None, None),
])
def test_parse_merge_command_extracts_the_target(command, pr, repo):
    got = lib.parse_merge_command(command)
    assert got is not None, command
    assert got["pr"] == pr
    assert got["repo"] == repo


@pytest.mark.parametrize("command", [
    "gh pr create",
    "gh pr view 1",
    "git merge",
    "gh repo merge",
    "python3 hooks/launcher_lib.py broker-merge --pr 1 --issue 2 --campaign c",
    "",
])
def test_parse_merge_command_rejects_non_merges(command):
    assert lib.parse_merge_command(command) is None


def test_disposed_statuses_mirror_driver_lib():
    """Mirrored constant, drift-guarded — the established pattern (CLAUDE.md §4 #21).

    driver_lib is 3945 lines and this hook runs on every Bash call, so the set is copied
    rather than imported. This test is what keeps the copy honest.
    """
    sys.path.insert(0, str(REPO_ROOT / "hooks"))
    import driver_lib  # noqa: PLC0415
    assert lib.DISPOSED_STATUSES == frozenset(driver_lib._DISPOSED_STATUSES)


def test_reads_are_bounded():
    """Finding F5: the platform's timeout behavior is unknown, so never approach it."""
    assert lib.MAX_STATE_BYTES <= 2 * 1024 * 1024
    assert lib.MAX_STATE_FILES <= 256
    assert lib.INTERNAL_DEADLINE_S < 5, "must answer before the registered 5 s cutoff"


def test_internal_deadline_denies_a_classified_merge(tmp_path):
    """Hitting the deadline post-classification refuses, per the AC3 split."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    target = lib.parse_merge_command("gh pr merge 887")
    decision = lib.decide(target, [], ["<deadline>"], CONFIGURED_REPO)
    assert decision["action"] == "deny"


# ── Step 8a review findings, each pinned by the case that exposed it ──────

def test_f1_a_json_file_past_the_read_cap_is_not_silently_dropped(tmp_path):
    """Files beyond MAX_STATE_FILES used to vanish, allowing the merge they governed.

    `.lock` siblings live in this directory too, and used to consume the budget before
    the cap was applied to state files only.
    """
    root = _project(tmp_path)
    state_dir = root / "claude_docs" / ".driver-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    for i in range(lib.MAX_STATE_FILES + 5):
        _campaign(root, "campaign-%03d" % i,
                  [{"number": 1, "status": "merged", "pr": 1}])
    # The active campaign sorts last, past the cap.
    _campaign(root, "zzz-active", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision("gh pr merge 887", root) == "deny"


def test_f1_lock_files_do_not_consume_the_read_budget(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    state_dir = root / "claude_docs" / ".driver-state"
    for i in range(lib.MAX_STATE_FILES + 10):
        (state_dir / ("c1-%03d.json.lock" % i)).write_text("", encoding="utf-8")
    active, unevaluable = lib.read_campaigns(str(root))
    assert unevaluable == []
    assert [c["campaign"] for c in active] == ["c1"]


def test_f2_an_error_after_classification_denies(tmp_path, monkeypatch):
    """The outer handler must refuse once the command is a known raw merge."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    hook = REPO_ROOT / "hooks" / HOOK
    # Break a function the hook calls only AFTER classification.
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'hooks')!r})\n"
        "import campaign_merge_guard_lib as g\n"
        "def boom(*a, **k):\n"
        "    raise RuntimeError('injected')\n"
        "g.read_campaigns = boom\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": "gh pr merge 887"},
                          "cwd": str(root)})
    proc = subprocess.run([sys.executable, str(hook)], input=payload, env=env,
                          capture_output=True, text=True, timeout=15, cwd=str(root))
    parsed = parse_hook_output(proc.stdout)
    assert parsed is not None, f"expected a deny, got stdout={proc.stdout!r}"
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "broker-merge" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_f3_a_cd_into_a_campaign_project_is_evaluated_there(tmp_path):
    """`cd /campaign && gh pr merge 887` runs in /campaign, not in the hook's cwd."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    outside = tmp_path / "outside"
    outside.mkdir()
    assert _decision(f"cd {root} && gh pr merge 887", outside) == "deny"


def test_f3_a_bare_merge_from_outside_any_project_still_allows(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    assert _decision("gh pr merge 887", outside) == "allow"


def test_f3_an_unresolvable_cd_target_denies(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision('cd "$TARGET" && gh pr merge 887', root) == "deny"


def test_f4_a_campaign_object_without_an_issues_list_is_unevaluable(tmp_path):
    """Valid JSON is not valid state; reading it as 'settled' allowed the merge."""
    root = _project(tmp_path)
    state_dir = root / "claude_docs" / ".driver-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "c1.json").write_text(json.dumps({"campaign": "c1"}), encoding="utf-8")
    assert _decision("gh pr merge 887", root) == "deny"


@pytest.mark.parametrize("state,expected", [
    ({"issues": [{"number": 1, "status": "pr_open"}]}, "active"),
    ({"issues": [{"number": 1, "status": "merged"}]}, "settled"),
    ({"issues": []}, "settled"),
    ({"campaign": "c"}, "invalid"),
    ({"issues": "nope"}, "invalid"),
    ({"issues": [None]}, "invalid"),
    ("not a dict", "invalid"),
])
def test_f4_campaign_activity_is_three_valued(state, expected):
    assert lib.campaign_activity(state) == expected


@pytest.mark.parametrize("command,pr", [
    ("gh pr merge --subject 2026 887", 887),
    ("gh pr merge -t 2026 887", 887),
    ("gh pr merge --body 12345 887 --squash", 887),
    ("gh pr merge --match-head-commit 999 887", 887),
    ("gh pr merge --subject=2026 887", 887),
])
def test_f5_a_flag_value_is_never_mistaken_for_the_pr(command, pr):
    """The first number after `merge` used to win, so `--subject 2026 887` read PR 2026."""
    assert lib.parse_merge_command(command)["pr"] == pr


def test_f5_a_non_numeric_positional_yields_an_unknown_target():
    """`gh pr merge my-branch` is a real form; it is unknown, not absent."""
    got = lib.parse_merge_command("gh pr merge my-feature-branch")
    assert got is not None and got["pr"] is None


@pytest.mark.parametrize("command", [
    "echo 'note; gh pr merge 887'",
    'echo "wrap; gh pr merge 887"',
    "cat <<'EOF'\ngh pr merge 887\nEOF",
])
def test_f6_a_separator_inside_quoted_text_is_not_a_separator(tmp_path, command):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision(command, root) == "allow"


def test_f7_a_missing_command_field_emits_a_diagnostic(tmp_path):
    root = _project(tmp_path)
    _stdout, stderr, rc = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {}},
                                   cwd=root)
    assert rc == 0
    assert "campaign-merge-guard" in stderr


def test_f8_the_replacement_command_names_the_real_project_root(tmp_path):
    """A literal `.` is wrong exactly when the call ran in a subdirectory."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    sub = root / "deep" / "nested"
    sub.mkdir(parents=True)
    stdout, _stderr, _rc = _guard("gh pr merge 887", sub)
    reason = parse_hook_output(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "--project-root %s" % root in reason
    assert "--project-root .\n" not in reason


# ── Step 11 review findings ───────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    'gh pr merge 887 --repo "3D-Stories/rawgentic" --squash',
    "gh pr merge 887 --repo '3D-Stories/rawgentic'",
    'gh pr merge --subject "fix 2026 bug" 887',
    'gh pr merge 887 --body "merging 2026 changes"',
])
def test_s11_f1_a_quoted_argument_value_survives(tmp_path, command):
    """Stripping quotes wholesale turned `--repo "o/r" --squash` into `--repo --squash`,
    read `--squash` as the repository, called it foreign, and allowed the merge."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision(command, root) == "deny"


def test_s11_f1_a_value_flag_never_adopts_the_next_flag_as_its_value():
    got = lib.parse_merge_command("gh pr merge 887 --repo --squash")
    assert got["repo"] is None
    assert got["pr"] == 887


def test_s11_f2_every_merge_target_is_checked(tmp_path):
    """A harmless first merge must not clear the whole Bash call."""
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision("gh pr merge 999; gh pr merge 887", root) == "deny"
    assert _decision("gh pr merge 999 && gh pr merge 887", root) == "deny"


def test_s11_f2_parse_returns_targets_in_execution_order():
    got = lib.parse_merge_command("gh pr merge 999; gh pr merge 887")
    assert [t["pr"] for t in got["targets"]] == [999, 887]


def test_s11_f2_several_unrelated_merges_still_allow(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision("gh pr merge 111; gh pr merge 222", root) == "allow"


@pytest.mark.parametrize("entry", [
    {"number": 880, "status": "pr_open", "pr": "887"},   # pr as a string
    {"number": "880", "status": "pr_open", "pr": 887},   # number as a string
    {"number": 880, "status": "pr_open", "pr": True},    # bool is not a PR
    {"number": 880, "pr": 887},                          # no status
    {"number": 880, "status": "", "pr": 887},            # empty status
])
def test_s11_f3_a_wrongly_typed_entry_is_unevaluable(tmp_path, entry):
    """`{"pr": "887"}` was read as ACTIVE, never matched integer 887, and allowed."""
    root = _project(tmp_path)
    _campaign(root, "c1", [entry])
    assert _decision("gh pr merge 887", root) == "deny"


def test_s11_f3_a_queued_child_with_no_pr_yet_is_still_valid():
    """A child that has not opened its PR carries no `pr` — that is normal, not corrupt."""
    assert lib.valid_issue_entry({"number": 880, "status": "queued"}) is True
    assert lib.campaign_activity({"issues": [{"number": 880, "status": "queued"}]}) \
        == "active"


def test_s11_f3_real_campaign_files_are_never_invalid():
    """The guard must not refuse legitimate state that exists on this host right now."""
    import glob  # noqa: PLC0415
    files = sorted(glob.glob(str(REPO_ROOT / "claude_docs" / ".driver-state" / "*.json")))
    if not files:
        pytest.skip("no live campaign state on this host")
    for path in files:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        assert lib.campaign_activity(state) != "invalid", path


def test_an_unlexable_command_denies_while_a_campaign_is_active(tmp_path):
    root = _project(tmp_path)
    _campaign(root, "c1", [{"number": 880, "status": "pr_open", "pr": 887}])
    assert _decision('gh pr merge 887 --subject "unbalanced', root) == "deny"


def test_an_unlexable_command_allows_when_no_campaign_is_active(tmp_path):
    """Unparseable is only a problem when there is something to protect."""
    root = _project(tmp_path)
    assert _decision('gh pr merge 887 --subject "unbalanced', root) == "allow"


# ── registration ──────────────────────────────────────────────────────────

def test_the_hook_is_registered_as_a_pretooluse_bash_hook():
    entries = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [
        h.get("command", "")
        for group in entries["PreToolUse"] if "Bash" in group.get("matcher", "")
        for h in group.get("hooks", [])
    ]
    assert any(c.endswith(f"/hooks/{HOOK}") for c in commands), commands


def test_the_hook_file_is_executable():
    assert os.access(REPO_ROOT / "hooks" / HOOK, os.X_OK)
