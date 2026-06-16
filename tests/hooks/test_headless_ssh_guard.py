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
    # Bypass forms an agent plausibly emits (Step 8a High finding) — must block:
    ("bash -c", "bash -c 'ssh host whoami'"),
    ("sh -c", 'sh -c "ssh host reboot"'),
    ("sudo bash -c", "sudo bash -c 'ssh host'"),
    ("eval", 'eval "ssh host"'),
    ("command substitution", "x=$(ssh host whoami)"),
    ("command subst inline", "echo $(ssh host hostname)"),
    # env-assignment whose $(...) value has a SPACE must not fragment detection
    # (Step 11 High): the real ssh after the assignment must still be caught.
    ("env-assign subst-space ssh", "TS=$(date +%s) ssh deploy@dev-vm 'systemctl restart api'"),
    ("env-assign subst-space rsync", "D=$(date +%F) rsync -av dist/ dev-vm:/var/www/"),
    ("env-assign subst-space scp", "H=$(cat host.txt) scp f dev-vm:/p"),
    # combined interpreter short-flags (Step 11 Low): -lc / -ec / -cx carry -c.
    ("bash -lc", "bash -lc 'ssh host whoami'"),
    ("sh -ec", "sh -ec 'scp f host:/p'"),
    ("bash -cx", 'bash -cx "ssh host"'),
    ("backtick", "echo `ssh host id`"),
    ("subshell", "(ssh host)"),
    ("process substitution", "diff <(ssh host cat /etc/hosts) local"),
    ("find -exec", "find . -name '*.log' -exec scp {} host:/logs/ ;"),
    ("timeout signal opt then ssh", "timeout -s KILL 5 ssh host"),
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


class TestConservativeOverBlock:
    """The wrapper scan is deliberately broad (fail-toward-blocking): once a known
    command-wrapper leads the segment, an ssh-family basename ANYWHERE after it is
    blocked, without parsing each wrapper's option arity. This avoids bypasses
    (`timeout -s KILL 5 ssh`, `sudo -u user ssh`) at the cost of over-blocking a
    few benign commands where ssh/scp/rsync is merely an ARGUMENT under a wrapper.
    These cases are pinned as INTENTIONAL — the cost is acceptable: it only bites
    in headless mode, and `headlessAllowSSH:true` is the explicit escape hatch."""

    @pytest.mark.parametrize("command", [
        "timeout 5 grep -rn rsync src/",   # grep for "rsync" under timeout
        "sudo cat ssh",                    # cat a file literally named ssh
        "timeout 10 cat /usr/bin/ssh",     # inspect the ssh binary under timeout
    ])
    def test_wrapper_over_blocks_ssh_arg(self, command):
        from headless_ssh_guard import detect_blocked_program
        assert detect_blocked_program(command) is not None


class TestKnownGaps:
    """Documented residual gaps — the matcher is a conservative safety net, not a
    sandbox; these forms are not blocked (and headlessAllowSSH:true is the explicit
    escape hatch for projects that genuinely need remote ops)."""

    def test_foreign_interpreter_eval_is_a_gap(self):
        # ssh embedded in a NON-shell interpreter string (python/perl/node -e) is
        # out of scope — only shell interpreters (bash/sh/...) and `eval` recurse.
        # The argument below is INERT TEST DATA (a string literal asserting the
        # matcher returns None) — it is never executed.
        from headless_ssh_guard import detect_blocked_program
        assert detect_blocked_program("python3 -c \"import os;os.system('ssh h')\"") is None


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
