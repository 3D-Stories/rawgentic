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


def _run_guard(command: str, cwd: Path | None = None) -> str:
    """Run wal-guard with *command* and return 'allow' or 'deny'.

    Pass *cwd* to isolate the subprocess from any ambient
    .rawgentic_workspace.json above the test runner's working directory.
    Without it, the hook's wal_find_workspace walks up from pytest's CWD
    and may pick up an active project whose protectionLevel changes the
    expected guard behavior.
    """
    stdout, _stderr, _rc = run_hook(HOOK, _make_input(command), cwd=cwd)
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
def test_wal_guard_pattern(
    label: str, command: str, expected: str, tmp_path: Path
) -> None:
    """Wal-guard correctly allows or denies *command*."""
    actual = _run_guard(command, cwd=tmp_path)
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

    def test_no_workspace_blocks_ssh_prod(self, tmp_path):
        """Without a workspace, wal-guard still blocks prod commands (strict default)."""
        actual = _run_guard("ssh user@prod-host", cwd=tmp_path)
        assert actual == "deny"

    def test_no_workspace_allows_safe_commands(self, tmp_path):
        actual = _run_guard("echo hello", cwd=tmp_path)
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


# ── Issue #47: headless blanket SSH block ────────────────────────────────

def _run_guard_headless(
    command, make_workspace, *, allow_ssh=None, level="standard", headless=True,
):
    """Run wal-guard with a session bound to testproj, optionally headless.

    `headlessAllowSSH` is WORKSPACE-scoped (sibling of headlessEnabled in the
    project's .rawgentic_workspace.json entry), so it is set via the project
    entry, NOT the per-project .rawgentic.json.
    """
    session_id = "headless-guard-sess"
    entry = {
        "name": "testproj", "path": "./projects/testproj",
        "active": True, "configured": True,
    }
    if allow_ssh is not None:
        entry["headlessAllowSSH"] = allow_ssh
    ws = make_workspace(
        projects=[entry],
        registry_entries=[{
            "session_id": session_id, "project": "testproj",
            "project_path": "./projects/testproj",
        }],
        project_configs={"testproj": {"protectionLevel": level}},
    )
    payload = {
        "tool_input": {"command": command}, "tool_name": "Bash",
        "session_id": session_id, "tool_use_id": "tu-h", "cwd": str(ws.root),
    }
    env = {"RAWGENTIC_HEADLESS": "1"} if headless else None
    stdout, _stderr, _rc = run_hook(HOOK, payload, cwd=ws.root, env_override=env)
    parsed = parse_hook_output(stdout)
    decision = "allow"
    if parsed and parsed.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        decision = "deny"
    return decision, parsed


class TestHeadlessSSHBlock:
    """Issue #47 Layer B — blanket ssh/scp/rsync/sftp block in headless mode."""

    SSH_CMDS = [
        ("ssh", "ssh deploy@host"),
        ("scp", "scp build.tar.gz host:/opt/app/"),
        ("rsync", "rsync -av dist/ host:/var/www/"),
        ("sftp", "sftp host"),
        ("sudo ssh", "sudo ssh host"),
        ("piped ssh", "tar czf - d | ssh host 'tar xzf -'"),
        # bypass forms must also block end-to-end through the hook (Step 8a/11):
        ("bash -c ssh", "bash -c 'ssh deploy@host'"),
        ("cmd subst ssh", "OUT=$(ssh deploy@host hostname)"),
        ("env subst-space ssh", "TS=$(date +%s) ssh deploy@dev-vm uptime"),
    ]

    @pytest.mark.parametrize("label,command", SSH_CMDS, ids=[c[0] for c in SSH_CMDS])
    def test_headless_blocks_ssh_family_default(self, label, command, make_workspace):
        # allowSSH absent → fail-closed default → block
        decision, _ = _run_guard_headless(command, make_workspace)
        assert decision == "deny", f"[{label}] headless should block: {command}"

    def test_headless_blocks_even_under_sandbox(self, make_workspace):
        # Independent of protectionLevel: sandbox normally allows everything.
        decision, _ = _run_guard_headless("ssh deploy@host", make_workspace, level="sandbox")
        assert decision == "deny"

    def test_headless_allow_ssh_true_permits(self, make_workspace):
        decision, _ = _run_guard_headless("ssh deploy@host", make_workspace, allow_ssh=True)
        assert decision == "allow"

    def test_headless_allow_ssh_false_blocks(self, make_workspace):
        decision, _ = _run_guard_headless("ssh deploy@host", make_workspace, allow_ssh=False)
        assert decision == "deny"

    @pytest.mark.parametrize("command", [
        "git push origin main",
        "git push -u origin fix/47-headless-remote-ops-guard",
        "gh pr create --title x --body y",
    ])
    def test_headless_allows_git_gh(self, command, make_workspace):
        # git/gh use their own transport — must NOT be blocked even in headless.
        decision, _ = _run_guard_headless(command, make_workspace)
        assert decision == "allow", f"headless must not block: {command}"

    def test_non_headless_does_not_block_plain_ssh(self, make_workspace):
        # No RAWGENTIC_HEADLESS → headless block inactive; plain `ssh host` (no
        # "prod") under standard is allowed by the existing prod patterns.
        decision, _ = _run_guard_headless(
            "ssh deploy@host", make_workspace, headless=False, level="standard")
        assert decision == "allow"

    def test_non_headless_still_blocks_ssh_prod(self, make_workspace):
        # Existing behavior unchanged: ssh-to-prod blocked under strict.
        decision, _ = _run_guard_headless(
            "ssh deploy@prod-1", make_workspace, headless=False, level="strict")
        assert decision == "deny"

    def test_block_message_mentions_allowssh_override(self, make_workspace):
        _, parsed = _run_guard_headless("ssh deploy@host", make_workspace)
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert "headlessAllowSSH" in reason


class TestHeadlessGuardBlockAudit:
    """#263 (C20): a headless deny must append a GUARD_BLOCK audit line to the
    per-project WAL. Before the fix, deny() gated on WAL_FILE but nothing ever
    called wal_init_file — the audit path was dead code and blocks in
    bypassPermissions mode left no trace."""

    def _deny_and_read_wal(self, make_workspace, command="ssh deploy@host"):
        session_id = "headless-audit-sess"
        ws = make_workspace(
            projects=[{
                "name": "testproj", "path": "./projects/testproj",
                "active": True, "configured": True,
            }],
            registry_entries=[{
                "session_id": session_id, "project": "testproj",
                "project_path": "./projects/testproj",
            }],
            project_configs={"testproj": {"protectionLevel": "strict"}},
        )
        payload = {
            "tool_input": {"command": command}, "tool_name": "Bash",
            "session_id": session_id, "tool_use_id": "tu-a", "cwd": str(ws.root),
        }
        stdout, _stderr, _rc = run_hook(
            HOOK, payload, cwd=ws.root, env_override={"RAWGENTIC_HEADLESS": "1"})
        parsed = parse_hook_output(stdout)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        wal_file = ws.claude_docs / "wal" / "testproj.jsonl"
        return ws, wal_file

    def test_headless_deny_appends_guard_block(self, make_workspace):
        _, wal_file = self._deny_and_read_wal(make_workspace)
        assert wal_file.exists(), "headless deny must create the per-project WAL"
        entries = [json.loads(l) for l in wal_file.read_text().splitlines() if l.strip()]
        blocks = [e for e in entries if e.get("phase") == "GUARD_BLOCK"]
        assert blocks, "headless deny must append a GUARD_BLOCK audit line"
        entry = blocks[-1]
        assert entry["guard"] == "wal-guard"
        assert entry["session"] == "headless-audit-sess"
        assert "ssh" in entry["command"]

    def test_non_headless_deny_writes_no_audit(self, make_workspace):
        """Companion: without RAWGENTIC_HEADLESS the deny stays silent (audit is
        a headless-only contract)."""
        session_id = "interactive-sess"
        ws = make_workspace(
            projects=[{
                "name": "testproj", "path": "./projects/testproj",
                "active": True, "configured": True,
            }],
            registry_entries=[{
                "session_id": session_id, "project": "testproj",
                "project_path": "./projects/testproj",
            }],
            project_configs={"testproj": {"protectionLevel": "strict"}},
        )
        payload = {
            "tool_input": {"command": "ssh deploy@prod-1"}, "tool_name": "Bash",
            "session_id": session_id, "tool_use_id": "tu-b", "cwd": str(ws.root),
        }
        stdout, _stderr, _rc = run_hook(HOOK, payload, cwd=ws.root)
        parsed = parse_hook_output(stdout)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        wal_file = ws.claude_docs / "wal" / "testproj.jsonl"
        if wal_file.exists():
            entries = [json.loads(l) for l in wal_file.read_text().splitlines() if l.strip()]
            assert not [e for e in entries if e.get("phase") == "GUARD_BLOCK"]


class TestMultiDocumentStdin:
    """#266 Step 11 R2 catch: the strict one-document parse in
    wal_parse_fields must not flip the fail-closed guard open. Before the
    fix, a stdin holding two JSON documents made wal_parse_fields return
    non-zero under set -e, aborting the hook with no deny JSON — which
    Claude Code treats as allow."""

    def test_multi_document_stdin_still_denies(self, tmp_path):
        import subprocess as sp
        from tests.hooks.conftest import HOOKS_DIR
        raw = '{"foo":1} ' + json.dumps({
            "tool_input": {
                "command": "docker compose -f docker-compose.prod.yml up -d"
            },
            "tool_name": "Bash", "session_id": "s-multi",
            "tool_use_id": "tu-multi", "cwd": str(tmp_path),
        })
        result = sp.run(
            ["bash", str(HOOKS_DIR / "wal-guard")],
            input=raw, capture_output=True, text=True,
            timeout=10, cwd=str(tmp_path),
        )
        parsed = parse_hook_output(result.stdout)
        decision = (
            (parsed or {})
            .get("hookSpecificOutput", {})
            .get("permissionDecision", "")
        )
        assert decision == "deny", (
            f"multi-document stdin must still deny a prod-destroy command; "
            f"rc={result.returncode} stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )


class TestSingleCombinedGrep:
    """#267: the hot allow-path runs ONE combined-pattern grep instead of 12
    per-pattern greps (plus the unconditional rm-/tmp allowlist grep = 2
    spawns total for a clean command)."""

    def test_clean_command_spawns_two_greps(self, tmp_path):
        import shutil as _shutil
        import subprocess as sp
        from tests.hooks.conftest import HOOKS_DIR
        real_grep = _shutil.which("grep")
        assert real_grep
        count_file = tmp_path / "grep-count"
        shim_dir = tmp_path / "shimbin"
        shim_dir.mkdir()
        shim = shim_dir / "grep"
        shim.write_text(
            f'#!/usr/bin/env bash\necho x >> "{count_file}"\n'
            f'exec "{real_grep}" "$@"\n'
        )
        shim.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        payload = json.dumps({
            "tool_input": {"command": "echo hello world"},
            "tool_name": "Bash", "session_id": "s-grepcount",
            "tool_use_id": "tu-g", "cwd": str(tmp_path),
        })
        result = sp.run(
            ["bash", str(HOOKS_DIR / "wal-guard")],
            input=payload, capture_output=True, text=True,
            timeout=10, cwd=str(tmp_path), env=env,
        )
        assert result.returncode == 0, result.stderr
        assert parse_hook_output(result.stdout) is None, "clean cmd must allow"
        spawns = (
            len(count_file.read_text().splitlines())
            if count_file.exists()
            else 0
        )
        assert spawns == 2, (
            f"clean command spawned grep {spawns} times, want 2 "
            f"(rm-/tmp allowlist + ONE combined pattern pre-filter)"
        )

    def test_prefilter_grep_error_does_not_fail_open(self, tmp_path):
        """#267 Step 11 R2 catch: if the combined pre-filter grep errors
        (rc 2, e.g. a future malformed pattern edit), wal-guard must fall
        through to the per-pattern loop — never fast-path allow. The shim
        errors only on the union-sized pattern argument and delegates every
        normal grep, so a prod-destroy command must still be denied."""
        import shutil as _shutil
        import subprocess as sp
        from tests.hooks.conftest import HOOKS_DIR
        real_grep = _shutil.which("grep")
        shim_dir = tmp_path / "shimbin"
        shim_dir.mkdir()
        shim = shim_dir / "grep"
        shim.write_text(
            '#!/usr/bin/env bash\n'
            'for a in "$@"; do\n'
            '  if [ "${#a}" -gt 200 ]; then exit 2; fi\n'
            'done\n'
            f'exec "{real_grep}" "$@"\n'
        )
        shim.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        payload = json.dumps({
            "tool_input": {"command": "ssh deploy@prod-host"},
            "tool_name": "Bash", "session_id": "s-rc2",
            "tool_use_id": "tu-rc2", "cwd": str(tmp_path),
        })
        result = sp.run(
            ["bash", str(HOOKS_DIR / "wal-guard")],
            input=payload, capture_output=True, text=True,
            timeout=10, cwd=str(tmp_path), env=env,
        )
        parsed = parse_hook_output(result.stdout)
        decision = (
            (parsed or {})
            .get("hookSpecificOutput", {})
            .get("permissionDecision", "")
        )
        assert decision == "deny", (
            f"pre-filter grep error must fall through to the loop, not "
            f"allow; rc={result.returncode} stdout={result.stdout!r}"
        )


class TestHugeCommandDeny:
    """#310: deny() passed the full command as a single jq exec argument; a
    command over Linux MAX_ARG_STRLEN (~128KiB per argument) made the jq exec
    fail (E2BIG, rc 126), so NO deny JSON reached stdout and the fail-closed
    guard failed OPEN. The fix bounds the embedded command at deny() entry."""

    HUGE = "ssh user@prod-host " + "A" * 300_000

    def test_huge_command_still_denied(self, tmp_path):
        assert _run_guard(self.HUGE, cwd=tmp_path) == "deny"

    def test_huge_command_reason_bounded_and_marked(self, tmp_path):
        stdout, _stderr, _rc = run_hook(HOOK, _make_input(self.HUGE), cwd=tmp_path)
        parsed = parse_hook_output(stdout)
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert len(reason) < 5000, "reason must not embed the unbounded command"
        assert "[truncated:" in reason, "truncation must be visible, never silent"

    def test_small_command_reason_untruncated(self, tmp_path):
        stdout, _stderr, _rc = run_hook(
            HOOK, _make_input("ssh user@prod-host"), cwd=tmp_path)
        parsed = parse_hook_output(stdout)
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert "ssh user@prod-host" in reason
        assert "[truncated:" not in reason

    def test_headless_huge_deny_still_audits(self, make_workspace):
        """The GUARD_BLOCK audit jq call had the same unbounded --arg cmd."""
        session_id = "headless-huge-sess"
        ws = make_workspace(
            projects=[{
                "name": "testproj", "path": "./projects/testproj",
                "active": True, "configured": True,
            }],
            registry_entries=[{
                "session_id": session_id, "project": "testproj",
                "project_path": "./projects/testproj",
            }],
            project_configs={"testproj": {"protectionLevel": "strict"}},
        )
        payload = {
            "tool_input": {"command": self.HUGE}, "tool_name": "Bash",
            "session_id": session_id, "tool_use_id": "tu-huge", "cwd": str(ws.root),
        }
        stdout, _stderr, _rc = run_hook(
            HOOK, payload, cwd=ws.root, env_override={"RAWGENTIC_HEADLESS": "1"})
        parsed = parse_hook_output(stdout)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        wal_file = ws.claude_docs / "wal" / "testproj.jsonl"
        assert wal_file.exists()
        entries = [json.loads(l) for l in wal_file.read_text().splitlines() if l.strip()]
        blocks = [e for e in entries if e.get("phase") == "GUARD_BLOCK"]
        assert blocks, "huge-command headless deny must still append its audit line"
        assert len(blocks[-1]["command"]) < 5000


class TestDecisionSerializerFallback:
    """#310 adversarial-review High: the decision jq call was the single point
    of fail-open — ANY failure there (not just E2BIG) meant empty stdout =
    allow. deny() must emit a static, jq-free fallback decision when the
    serializer fails."""

    def test_decision_emitted_when_decision_jq_fails(self, tmp_path):
        import shutil
        real_jq = os.path.expanduser("~/.local/bin/jq")
        if not os.path.isfile(real_jq):
            real_jq = shutil.which("jq")
        assert real_jq, "test needs a real jq to delegate to"
        # wal-lib resolves $HOME/.local/bin/jq first, so a fake HOME with a
        # wrapper there deterministically intercepts every jq call. The
        # wrapper delegates to the real jq EXCEPT for the decision call
        # (recognized by its permissionDecision filter), which it fails.
        fake_home = tmp_path / "home"
        bindir = fake_home / ".local" / "bin"
        bindir.mkdir(parents=True)
        wrapper = bindir / "jq"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            'for a in "$@"; do\n'
            '  case "$a" in *permissionDecision*) exit 1;; esac\n'
            "done\n"
            f'exec "{real_jq}" "$@"\n'
        )
        wrapper.chmod(0o755)
        cwd = tmp_path / "work"
        cwd.mkdir()
        stdout, _stderr, _rc = run_hook(
            HOOK, _make_input("ssh user@prod-host"), cwd=cwd,
            env_override={"HOME": str(fake_home)})
        parsed = parse_hook_output(stdout)
        assert parsed is not None, \
            "a failing decision serializer must not suppress the deny"
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "fallback" in parsed["hookSpecificOutput"]["permissionDecisionReason"]
