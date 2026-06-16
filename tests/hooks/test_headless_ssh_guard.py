"""Tests for hooks/headless_ssh_guard.py — the headless blanket SSH-family matcher.

Issue #47 Layer B: in headless mode the WAL guard must DENY any command that
invokes an SSH-family program (ssh/scp/rsync/sftp), regardless of which WF2 step
initiates it. The detection is the security-critical, bypass-prone part, so it
lives in this pure, fully-unit-tested matcher; the env/config gating (is-headless,
allowSSH) stays in wal-guard/wal-lib.

The matcher detects the *program* — never a substring or an argument — so it must
NOT fire on `git push`/`gh pr` (which use their own transport) nor on `ssh` used as
a mere argument (`grep ssh`, `echo ssh`, `cat /etc/ssh/sshd_config`). It MUST fire
through common wrappers/prefixes the judges flagged: env-assignments, sudo, env,
timeout, nohup, command, xargs, and an absolute path to the binary.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

GUARD_CLI = HOOKS_DIR / "headless_ssh_guard.py"


# (label, command) pairs that MUST be detected as SSH-family invocations.
BLOCK_CASES = [
    ("bare ssh", "ssh user@host"),
    ("bare scp", "scp build.tar.gz host:/opt/app/"),
    ("bare rsync", "rsync -av dist/ host:/var/www/"),
    ("bare sftp", "sftp host"),
    ("ssh to prod", "ssh deploy@prod-1.example.com 'systemctl restart api'"),
    ("single env prefix", "FOO=bar ssh host"),
    ("multi env prefix", "FOO=bar BAZ=qux ssh host"),
    ("sudo wrapper", "sudo ssh host"),
    ("sudo with flag", "sudo -n ssh host"),
    ("sudo -u user", "sudo -u deploy ssh host"),
    ("timeout wrapper", "timeout 5 ssh host"),
    ("timeout duration suffix", "timeout 5s ssh host"),
    ("env wrapper", "env ssh host"),
    ("env wrapper with assign", "env FOO=bar ssh host"),
    ("nohup wrapper", "nohup ssh host"),
    ("command builtin", "command ssh host"),
    ("nested wrappers", "sudo timeout 5 ssh host"),
    ("absolute path", "/usr/bin/ssh host"),
    ("xargs wrapper", "xargs ssh"),
    ("after &&", "echo starting && ssh host"),
    ("after ;", "echo starting; ssh host"),
    ("after ||", "false || ssh host"),
    ("piped into ssh", "tar czf - dir | ssh host 'tar xzf -'"),
    ("after newline", "echo a\nssh host"),
    ("scp via sudo", "sudo scp f host:/p"),
]

# (label, command) pairs that MUST NOT be detected — common legitimate commands.
ALLOW_CASES = [
    ("git push", "git push origin main"),
    ("git push upstream", "git push -u origin fix/47-headless-remote-ops-guard"),
    ("gh pr create", "gh pr create --title x --body y"),
    ("git fetch", "git fetch origin"),
    ("ssh as echo arg", "echo ssh is the transport"),
    ("ssh as grep arg", "grep ssh /etc/services"),
    ("cat sshd_config", "cat /etc/ssh/sshd_config"),
    ("ls ssh dir", "ls -la /etc/ssh"),
    ("pytest ssh testfile", "pytest tests/test_ssh_guard.py -v"),
    ("ssh in quoted string", 'echo "remember to ssh later"'),
    ("rsync substring word", "python3 rsyncutil.py"),
    ("timeout non-ssh", "timeout 300 pytest tests/ -v"),
    ("sudo non-ssh", "sudo apt-get update"),
    ("empty", ""),
    ("whitespace", "   "),
]


class TestDetectBlockedProgram:
    @pytest.mark.parametrize("label,command", BLOCK_CASES, ids=[c[0] for c in BLOCK_CASES])
    def test_blocked(self, label, command):
        from headless_ssh_guard import detect_blocked_program
        prog = detect_blocked_program(command)
        assert prog in {"ssh", "scp", "rsync", "sftp"}, f"[{label}] expected block, got {prog!r}"

    @pytest.mark.parametrize("label,command", ALLOW_CASES, ids=[c[0] for c in ALLOW_CASES])
    def test_allowed(self, label, command):
        from headless_ssh_guard import detect_blocked_program
        prog = detect_blocked_program(command)
        assert prog is None, f"[{label}] expected allow, got blocked-by {prog!r}"

    def test_returns_specific_program(self):
        from headless_ssh_guard import detect_blocked_program
        assert detect_blocked_program("scp a host:/b") == "scp"
        assert detect_blocked_program("rsync -av a host:/b") == "rsync"


class TestCLI:
    def _run(self, command, timeout=10):
        import subprocess
        return subprocess.run(
            ["python3", str(GUARD_CLI), "detect"],
            input=command, capture_output=True, text=True, timeout=timeout,
        )

    def test_cli_block_prints_program_exit0(self):
        r = self._run("ssh user@host")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ssh"

    def test_cli_allow_empty_stdout_exit1(self):
        r = self._run("git push origin main")
        assert r.returncode == 1
        assert r.stdout.strip() == ""

    def test_cli_block_via_wrapper(self):
        r = self._run("sudo -u deploy ssh host")
        assert r.returncode == 0
        assert r.stdout.strip() == "ssh"
