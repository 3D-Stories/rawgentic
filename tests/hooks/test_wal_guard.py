"""Tests for hooks/wal-guard — PreToolUse guard that blocks dangerous Bash commands.

Ported from tests/test_wal_guard.sh. Covers:
- Parametrized allow/deny cases for production deployment denial and
  destructive local command passthrough
- Empty and missing command edge cases
- Fail-closed behavior when jq is unavailable
- Per-project protection level filtering (sandbox/standard/strict)
- /tmp allowlist
- Explicit guards.wal override
"""
import json
import os
from pathlib import Path

import pytest

from tests.hooks.conftest import parse_hook_output, run_hook, Workspace

HOOK = "wal-guard"


def _make_input(command: str) -> dict:
    """Build the stdin payload for wal-guard."""
    return {"tool_input": {"command": command}}


def _run_guard(command: str) -> str:
    """Run wal-guard with *command* and return 'allow' or 'deny'."""
    stdout, _stderr, _rc = run_hook(HOOK, _make_input(command))
    parsed = parse_hook_output(stdout)
    if parsed is None:
        return "allow"
    decision = (
        parsed.get("hookSpecificOutput", {}).get("permissionDecision", "")
    )
    if decision == "deny":
        return "deny"
    return "allow"


# ── Parametrized cases ported from test_wal_guard.sh ─────────────────────

# (label, command, expected)
CASES = [
    # AC1-3: Local file/git/gh operations → ALLOW
    ("git diff deploy-prod file", "git diff .github/workflows/deploy-prod.yml", "allow"),
    ("git commit with deploy-prod msg", 'git commit -m "fix deploy-prod workflow"', "allow"),
    ("git log grep deploy-prod", 'git log --grep="deploy-prod"', "allow"),
    ("git add deploy-prod file", "git add .github/workflows/deploy-prod.yml", "allow"),
    ("gh pr create with deploy-prod", 'gh pr create --body "updates deploy-prod pipeline"', "allow"),
    ("gh issue create deploy-prod", 'gh issue create --body "deploy-prod bug"', "allow"),
    ("cat deploy-prod file", "cat .github/workflows/deploy-prod.yml", "allow"),
    ("sed on deploy-prod file", "sed -i 's/old/new/' deploy-prod.yml", "allow"),
    ("GH_TOKEN export + gh pr", 'export GH_TOKEN=abc && gh pr create --body "deploy-prod"', "allow"),
    ("git checkout branch with deploy-prod", "git checkout -b fix/11-wal-guard-deploy-prod-pattern", "allow"),
    # AC4: Actual deployment commands → DENY
    ("ssh to prod host", "ssh user@prod-host", "deny"),
    ("ssh to staging.prod.example", "ssh user@staging.prod.example.com uptime", "deny"),
    ("scp to prod", "scp build.tar.gz prod-server:/opt/app/", "deny"),
    ("rsync to prod", "rsync -avz ./dist/ prod-host:/var/www/", "deny"),
    ("docker compose prod up", "docker compose -f docker-compose.prod.yml up -d", "deny"),
    ("kubectl prod context", "kubectl --context prod-cluster apply -f deploy.yml", "deny"),
    ("terraform apply prod", "terraform apply -var-file=prod.tfvars", "deny"),
    ("ansible prod", "ansible-playbook -i prod-inventory site.yml", "deny"),
    ("helm install prod", "helm install myapp ./chart --set env=prod", "deny"),
    ("ssh with compose prod up", 'ssh root@198.51.100.1 "docker compose -f /srv/app/docker-compose.sdlc.prod.yml up -d"', "deny"),
    # Destructive local commands — allowed (Claude asks user before running)
    ("rm -rf", "rm -rf /tmp/test", "allow"),
    ("git push --force", "git push --force origin main", "allow"),
    ("git reset --hard", "git reset --hard HEAD~1", "allow"),
    ("git commit --no-verify", 'git commit --no-verify -m "skip hooks"', "allow"),
    ("git checkout .", "git checkout .", "allow"),
    ("git clean -f", "git clean -fd", "allow"),
    ("git branch -D", "git branch -D old-feature", "allow"),
    ("normal git push (safe)", "git push origin feature-branch", "allow"),
    ("normal rm (safe)", "rm file.txt", "allow"),
    # Edge cases
    ("docker compose prod config (read-only)", "docker compose -f docker-compose.prod.yml config", "allow"),
    ("docker compose prod ps (read-only)", "docker compose -f docker-compose.prod.yml ps", "allow"),
    ("echo deploy prod (not a real command)", 'echo "deploy to prod"', "allow"),
    ("env var with prod (no deploy tool)", "export DEPLOY_ENV=prod && echo done", "allow"),
]


@pytest.mark.parametrize(
    "label, command, expected",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_wal_guard_pattern(label: str, command: str, expected: str) -> None:
    """Wal-guard correctly allows or denies *command*."""
    actual = _run_guard(command)
    assert actual == expected, f"[{label}] expected {expected}, got {actual}"


# ── Standalone edge-case tests ───────────────────────────────────────────


def test_empty_command_allowed() -> None:
    """An empty command string should be allowed (no pattern matches)."""
    assert _run_guard("") == "allow"


def test_missing_command_allowed() -> None:
    """If tool_input has no 'command' key, wal-guard should allow."""
    stdout, _stderr, _rc = run_hook(HOOK, {"tool_input": {}})
    parsed = parse_hook_output(stdout)
    assert parsed is None, f"Expected allow (no output), got: {stdout}"


def test_missing_jq_denies_all() -> None:
    """Without jq, wal-guard is fail-closed: even safe commands are denied.

    Since jq and bash typically share the same directory (/usr/bin), we
    can't simply strip jq's directory from PATH.  Instead we build a
    shadow bin/ with symlinks to every /usr/bin/* binary *except* jq,
    then replace /usr/bin in PATH with our shadow directory.

    Uses tempfile.mkdtemp instead of pytest's tmp_path to avoid the
    /tmp/pytest-of-<user> ownership issue on shared systems.
    """
    import json as _json
    import shutil
    import subprocess as _sp
    import tempfile

    from tests.hooks.conftest import HOOKS_DIR

    tmpdir = Path(tempfile.mkdtemp(prefix="wal-guard-nojq-"))
    try:
        # Build a shadow directory that mirrors /usr/bin minus jq
        shadow = tmpdir / "shadow_bin"
        shadow.mkdir()
        usr_bin = Path("/usr/bin")
        for entry in usr_bin.iterdir():
            if entry.name == "jq":
                continue
            target = shadow / entry.name
            try:
                target.symlink_to(entry)
            except OSError:
                pass  # skip broken/unresolvable entries

        # Ensure ~/.local/bin/jq doesn't exist (use a fake HOME)
        fake_home = tmpdir / "fakehome"
        fake_home.mkdir()

        # Build a modified PATH: replace /usr/bin with shadow, keep rest
        original_path = os.environ.get("PATH", "")
        new_dirs: list[str] = []
        for d in original_path.split(os.pathsep):
            if not d:
                continue
            if os.path.normpath(d) == "/usr/bin":
                new_dirs.append(str(shadow))
            elif os.path.isfile(os.path.join(d, "jq")):
                # Also shadow any other directory containing jq
                continue
            else:
                new_dirs.append(d)

        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(new_dirs)
        env["HOME"] = str(fake_home)

        hook_path = HOOKS_DIR / HOOK
        result = _sp.run(
            [str(shadow / "bash"), str(hook_path)],
            input=_json.dumps(_make_input("echo hello")),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        stdout = result.stdout
        parsed = parse_hook_output(stdout)
        assert parsed is not None, "Expected deny output when jq is missing"
        decision = parsed["hookSpecificOutput"]["permissionDecision"]
        assert decision == "deny"
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert "jq" in reason.lower(), f"Reason should mention jq: {reason}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Per-project protection level tests ────────────────────────────────────


def _run_guard_with_level(
    command: str,
    level: str,
    make_workspace,
    *,
    project_configs: dict | None = None,
) -> tuple[str, dict | None]:
    """Run wal-guard with a workspace configured at *level*.

    Creates a workspace with a project bound to the session, runs the
    guard, and returns (decision, parsed_output).
    """
    session_id = "guard-test-sess"
    if project_configs is None:
        project_configs = {"testproj": {"protectionLevel": level}}

    ws: Workspace = make_workspace(
        registry_entries=[{
            "session_id": session_id,
            "project": "testproj",
            "project_path": "./projects/testproj",
        }],
        project_configs=project_configs,
    )

    payload = {
        "tool_input": {"command": command},
        "tool_name": "Bash",
        "session_id": session_id,
        "tool_use_id": "tu-1",
        "cwd": str(ws.root),
    }

    stdout, _stderr, _rc = run_hook(HOOK, payload, cwd=ws.root)
    parsed = parse_hook_output(stdout)
    if parsed is None:
        return "allow", None
    decision = parsed.get("hookSpecificOutput", {}).get("permissionDecision", "")
    if decision == "deny":
        return "deny", parsed
    return "allow", parsed


class TestSandboxLevel:
    """Sandbox protection level allows ALL commands."""

    @pytest.mark.parametrize("command", [
        "ssh user@prod-host",
        "scp build.tar.gz prod-server:/opt/app/",
        "docker compose -f docker-compose.prod.yml down",
        "ansible-playbook -i prod-inventory site.yml",
        "kubectl delete pod mypod --context prod",
        "helm uninstall myapp --namespace prod",
        "terraform destroy -var-file=prod.tfvars",
        "rsync -avz ./dist/ prod-host:/var/www/",
    ])
    def test_sandbox_allows_all(self, command, make_workspace):
        decision, _ = _run_guard_with_level(command, "sandbox", make_workspace)
        assert decision == "allow", f"sandbox should allow: {command}"


class TestStandardLevel:
    """Standard level allows some ops and blocks others."""

    @pytest.mark.parametrize("label,command", [
        ("ssh prod", "ssh user@prod-host"),
        ("docker restart prod", "docker compose -f docker-compose.prod.yml restart"),
        ("kubectl get prod", "kubectl get pods --context prod"),
        ("ansible --check prod", "ansible-playbook --check -i prod-inventory site.yml"),
    ])
    def test_standard_allows(self, label, command, make_workspace):
        decision, _ = _run_guard_with_level(command, "standard", make_workspace)
        assert decision == "allow", f"standard should allow: {label}"

    @pytest.mark.parametrize("label,command", [
        ("scp prod", "scp build.tar.gz prod-server:/opt/app/"),
        ("docker rm prod", "docker compose -f docker-compose.prod.yml down"),
        ("ansible-playbook prod (no check)", "ansible-playbook -i prod-inventory site.yml"),
        ("kubectl delete prod", "kubectl delete pod mypod --context prod"),
        ("rsync prod", "rsync -avz ./dist/ prod-host:/var/www/"),
        ("helm uninstall prod", "helm uninstall myapp --namespace prod"),
        ("terraform destroy prod", "terraform destroy -var-file=prod.tfvars"),
    ])
    def test_standard_blocks(self, label, command, make_workspace):
        decision, _ = _run_guard_with_level(command, "standard", make_workspace)
        assert decision == "deny", f"standard should block: {label}"


class TestStrictLevel:
    """Strict level blocks all 12 patterns."""

    @pytest.mark.parametrize("label,command", [
        ("ssh prod", "ssh user@prod-host"),
        ("scp prod", "scp build.tar.gz prod-server:/opt/app/"),
        ("rsync prod", "rsync -avz ./dist/ prod-host:/var/www/"),
        ("docker up prod", "docker compose up -d --file prod.yml"),
        ("docker down prod", "docker compose -f docker-compose.prod.yml down"),
        ("ansible prod", "ansible-playbook -i prod-inventory site.yml"),
        ("kubectl apply prod", "kubectl apply -f deploy.yml --context prod"),
        ("kubectl delete prod", "kubectl delete pod mypod --context prod"),
        ("helm install prod", "helm install myapp ./chart --set env=prod"),
        ("helm uninstall prod", "helm uninstall myapp --namespace prod"),
        ("terraform apply prod", "terraform apply -var-file=prod.tfvars"),
        ("terraform destroy prod", "terraform destroy -var-file=prod.tfvars"),
    ])
    def test_strict_blocks_all(self, label, command, make_workspace):
        decision, _ = _run_guard_with_level(command, "strict", make_workspace)
        assert decision == "deny", f"strict should block: {label}"

    @pytest.mark.parametrize("label,command", [
        ("docker logs prod", "docker logs prod-container"),
        ("kubectl get prod", "kubectl get pods --context prod"),
    ])
    def test_strict_allows_read_commands(self, label, command, make_workspace):
        decision, _ = _run_guard_with_level(command, "strict", make_workspace)
        assert decision == "allow", f"strict should allow read command: {label}"


class TestTmpAllowlist:
    """rm on /tmp is always allowed regardless of protection level."""

    @pytest.mark.parametrize("level", ["sandbox", "standard", "strict"])
    def test_rm_tmp_allowed(self, level, make_workspace):
        decision, _ = _run_guard_with_level("rm -f /tmp/foo", level, make_workspace)
        assert decision == "allow", f"rm /tmp should be allowed at {level}"

    def test_rm_tmp_dir_allowed(self, make_workspace):
        decision, _ = _run_guard_with_level("rm -rf /tmp", "strict", make_workspace)
        assert decision == "allow"


class TestNoWorkspaceBackwardCompat:
    """Missing workspace config = strict behavior (backward compat)."""

    def test_no_workspace_blocks_ssh_prod(self):
        """Without a workspace, wal-guard still blocks prod commands (strict default)."""
        actual = _run_guard("ssh user@prod-host")
        assert actual == "deny"

    def test_no_workspace_allows_safe_commands(self):
        actual = _run_guard("echo hello")
        assert actual == "allow"


class TestDenyMessage:
    """Deny output includes the original command text."""

    def test_deny_contains_command(self, make_workspace):
        command = "ssh user@prod-host"
        decision, parsed = _run_guard_with_level(command, "strict", make_workspace)
        assert decision == "deny"
        assert parsed is not None
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert command in reason, f"Deny reason should contain the command: {reason}"
        assert "Run this command manually" in reason


class TestExplicitGuardsWal:
    """Explicit guards.wal array overrides protectionLevel."""

    def test_only_blocks_specified_rules(self, make_workspace):
        """guards.wal: ["ssh-prod"] should only block ssh, not scp."""
        # ssh-prod should be blocked
        decision, _ = _run_guard_with_level(
            "ssh user@prod-host",
            "sandbox",  # level is ignored when guards.wal is set
            make_workspace,
            project_configs={"testproj": {"guards": {"wal": ["ssh-prod"]}}},
        )
        assert decision == "deny", "ssh-prod should be blocked by explicit guard"

        # scp-prod should be allowed (not in the explicit list)
        decision, _ = _run_guard_with_level(
            "scp build.tar.gz prod-server:/opt/app/",
            "sandbox",
            make_workspace,
            project_configs={"testproj": {"guards": {"wal": ["ssh-prod"]}}},
        )
        assert decision == "allow", "scp-prod should be allowed when not in explicit guards"

    def test_explicit_empty_is_not_triggered(self, make_workspace):
        """guards.wal with only empty array should not override (treated as no config).

        Note: An empty guards.wal array means the jq join produces empty string,
        which the shell treats as unset, falling back to protectionLevel.
        """
        # Empty guards.wal array -- falls through to protectionLevel
        decision, _ = _run_guard_with_level(
            "ssh user@prod-host",
            "strict",  # unused since project_configs overrides
            make_workspace,
            project_configs={"testproj": {
                "protectionLevel": "sandbox",
                "guards": {"wal": []},
            }},
        )
        assert decision == "allow", "empty guards.wal falls through to protectionLevel sandbox"


class TestAnsibleExcludePattern:
    """Ansible --check/--diff/--syntax-check/--list-hosts/--list-tasks bypass."""

    @pytest.mark.parametrize("flag", [
        "--check",
        "--diff",
        "--syntax-check",
        "--list-hosts",
        "--list-tasks",
    ])
    def test_ansible_check_flags_allowed_strict(self, flag, make_workspace):
        """ansible-playbook with safety flags should be allowed even at strict."""
        command = f"ansible-playbook {flag} -i prod-inventory site.yml"
        decision, _ = _run_guard_with_level(command, "strict", make_workspace)
        assert decision == "allow", f"ansible {flag} should be allowed"

    def test_ansible_without_check_denied_strict(self, make_workspace):
        command = "ansible-playbook -i prod-inventory site.yml"
        decision, _ = _run_guard_with_level(command, "strict", make_workspace)
        assert decision == "deny"
