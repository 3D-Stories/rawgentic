"""Tests for hooks/wal-guard — PreToolUse guard that blocks dangerous Bash commands.

Ported from tests/test_wal_guard.sh. Covers:
- Parametrized allow/deny cases for all 30 original patterns
- Empty and missing command edge cases
- Fail-closed behavior when jq is unavailable
"""
import os
from pathlib import Path

import pytest

from tests.hooks.conftest import parse_hook_output, run_hook

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
    ("ssh with compose prod up", 'ssh root@10.0.17.202 "docker compose -f /srv/app/docker-compose.sdlc.prod.yml up -d"', "deny"),
    # Existing patterns still work
    ("rm -rf", "rm -rf /tmp/test", "deny"),
    ("git push --force", "git push --force origin main", "deny"),
    ("git reset --hard", "git reset --hard HEAD~1", "deny"),
    ("git commit --no-verify", 'git commit --no-verify -m "skip hooks"', "deny"),
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
