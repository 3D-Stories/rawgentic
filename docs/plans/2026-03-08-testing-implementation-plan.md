# Hook Test Suite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement comprehensive pytest test suite for all untested rawgentic hooks, guided by issue #9 and the testing strategy in `docs/plans/2026-03-08-testing-strategy.md`.

**Architecture:** Subprocess black-box tests invoke bash/python hooks via `subprocess.run()`, piping JSON stdin and asserting on stdout + filesystem side effects. Shared fixtures in conftest.py provide workspace scaffolding, hook invocation, and jq-absent environment simulation.

**Tech Stack:** Python 3.10+, pytest, subprocess, tempfile, json, pathlib

---

### Task 1: Create Test Branch

**Step 1: Ensure clean working tree**

Run: `cd $PROJECT_ROOT && git status --porcelain`
Expected: Only untracked files (no staged/modified tracked files)

**Step 2: Fetch and create branch**

```bash
git fetch origin
git checkout -b test/comprehensive-hook-suite origin/main
```

**Step 3: Commit design docs**

```bash
git add docs/plans/2026-03-08-testing-strategy.md docs/plans/2026-03-08-testing-implementation-plan.md
git commit -m "docs: add testing strategy and implementation plan for issue #9"
```

---

### Task 2: Build Shared Fixtures (conftest.py)

**Files:**
- Create: `tests/hooks/conftest.py`
- Reference: `hooks/wal-lib.sh`, `hooks/wal-guard`, `tests/hooks/test_security_guard_e2e.py`

**Step 1: Write conftest.py with all three fixtures**

```python
"""Shared fixtures for hook tests."""
import json
import os
import shutil
import subprocess
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"


@dataclass
class Workspace:
    """Represents a temporary rawgentic workspace for testing."""
    root: Path
    workspace_json: Path
    registry: Path
    wal_dir: Path
    notes_dir: Path


@pytest.fixture
def make_workspace(tmp_path):
    """Factory fixture that creates a temp workspace with configurable state.

    Args:
        projects: List of project dicts with name, path, active, configured keys.
        registry_entries: List of session registry dicts (session_id, project, project_path).
        wal_entries: Dict mapping project name to list of WAL JSONL dicts.
        session_notes: Dict mapping project name to notes file content string.
        create_project_dirs: Whether to create project directories on disk (default True).
    """
    def _factory(
        projects=None,
        registry_entries=None,
        wal_entries=None,
        session_notes=None,
        create_project_dirs=True,
    ):
        root = tmp_path / "workspace"
        root.mkdir()

        # Default: single active project
        if projects is None:
            projects = [
                {"name": "testproj", "path": "./projects/testproj",
                 "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ]

        # Write .rawgentic_workspace.json
        ws_json = root / ".rawgentic_workspace.json"
        ws_json.write_text(json.dumps({
            "version": 1,
            "projectsDir": "./projects",
            "projects": projects,
        }))

        # Create project directories on disk
        if create_project_dirs:
            for p in projects:
                proj_dir = root / p["path"].lstrip("./")
                proj_dir.mkdir(parents=True, exist_ok=True)

        # Create claude_docs structure
        claude_docs = root / "claude_docs"
        claude_docs.mkdir()
        wal_dir = claude_docs / "wal"
        wal_dir.mkdir()
        notes_dir = claude_docs / "session_notes"
        notes_dir.mkdir()

        # Write session registry
        registry = claude_docs / "session_registry.jsonl"
        if registry_entries:
            lines = [json.dumps(e) for e in registry_entries]
            registry.write_text("\n".join(lines) + "\n")
        else:
            registry.write_text("")

        # Write WAL entries per project
        if wal_entries:
            for proj_name, entries in wal_entries.items():
                wal_file = wal_dir / f"{proj_name}.jsonl"
                lines = [json.dumps(e) for e in entries]
                wal_file.write_text("\n".join(lines) + "\n")

        # Write session notes per project
        if session_notes:
            for proj_name, content in session_notes.items():
                notes_file = notes_dir / f"{proj_name}.md"
                notes_file.write_text(content)

        return Workspace(
            root=root,
            workspace_json=ws_json,
            registry=registry,
            wal_dir=wal_dir,
            notes_dir=notes_dir,
        )

    return _factory


def run_hook(hook_name, stdin_dict, *, cwd=None, env_override=None, timeout=10):
    """Invoke a hook script via subprocess.

    Determines interpreter from file extension (.py -> python3, else bash).
    Injects cwd into stdin_dict if not present.
    Always captures stderr for debugging.

    Returns (stdout: str, stderr: str, returncode: int).
    """
    hook_path = HOOKS_DIR / hook_name

    # Determine interpreter
    if hook_path.suffix == ".py":
        cmd = ["python3", str(hook_path)]
    elif hook_path.suffix == ".sh":
        cmd = ["bash", str(hook_path)]
    else:
        cmd = ["bash", str(hook_path)]

    # Inject cwd if not present
    if cwd and "cwd" not in stdin_dict:
        stdin_dict = {**stdin_dict, "cwd": str(cwd)}

    env = os.environ.copy()
    if env_override:
        env.update(env_override)

    result = subprocess.run(
        cmd,
        input=json.dumps(stdin_dict),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return result.stdout, result.stderr, result.returncode


def parse_hook_output(stdout):
    """Parse JSON from hook stdout. Returns dict or None if empty/invalid."""
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


@pytest.fixture
def no_jq_env():
    """Returns env dict with PATH stripped of jq locations.

    Removes directories containing jq from PATH so hooks see jq as unavailable.
    """
    original_path = os.environ.get("PATH", "")
    filtered = ":".join(
        d for d in original_path.split(":")
        if not (Path(d) / "jq").exists()
    )
    # Also exclude ~/.local/bin which hooks check explicitly
    home = os.environ.get("HOME", "")
    local_bin = str(Path(home) / ".local" / "bin")
    filtered = ":".join(
        d for d in filtered.split(":")
        if d != local_bin
    )
    return {"PATH": filtered}
```

**Step 2: Verify fixtures import correctly**

Run: `cd $PROJECT_ROOT && python3 -c "import tests.hooks.conftest"`
Expected: No errors (may need `tests/__init__.py` and `tests/hooks/__init__.py`)

**Step 3: Commit**

```bash
git add tests/hooks/conftest.py
git commit -m "test: add shared fixtures for hook test suite

Adds make_workspace factory, run_hook subprocess helper, no_jq_env
PATH manipulation, and parse_hook_output utility."
```

---

### Task 3: test_security_guard_check.py (simplest hook)

**Files:**
- Create: `tests/hooks/test_security_guard_check.py`
- Reference: `hooks/security-guard-check.sh`

**Step 1: Write tests**

```python
"""Tests for security-guard-check.sh SessionStart hook."""
import json
import os
from pathlib import Path

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


class TestSecurityGuardCheck:
    """Tests that the hook detects conflict with official security-guidance plugin."""

    def _run(self, settings_content=None, *, home_dir):
        """Helper: write settings.json and run the hook."""
        claude_dir = home_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        if settings_content is not None:
            (claude_dir / "settings.json").write_text(
                json.dumps(settings_content)
            )
        return run_hook(
            "security-guard-check.sh",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            env_override={"HOME": str(home_dir)},
        )

    def test_conflict_detected(self, tmp_path):
        """Official plugin enabled -> emits warning."""
        settings = {
            "enabledPlugins": {
                "security-guidance@claude-plugins-official": True,
            }
        }
        stdout, stderr, rc = self._run(settings, home_dir=tmp_path)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        assert "security-guidance" in output.get("systemMessage", "") or \
               "security-guidance" in json.dumps(output)

    def test_no_conflict(self, tmp_path):
        """Official plugin not enabled -> silent exit."""
        settings = {"enabledPlugins": {}}
        stdout, stderr, rc = self._run(settings, home_dir=tmp_path)
        assert rc == 0
        assert parse_hook_output(stdout) is None

    def test_settings_missing(self, tmp_path):
        """No settings.json -> silent exit."""
        stdout, stderr, rc = self._run(home_dir=tmp_path)
        assert rc == 0
        assert parse_hook_output(stdout) is None

    def test_malformed_settings(self, tmp_path):
        """Invalid JSON in settings.json -> silent exit."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text("not json {{{")
        stdout, stderr, rc = run_hook(
            "security-guard-check.sh",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            env_override={"HOME": str(tmp_path)},
        )
        assert rc == 0
        assert parse_hook_output(stdout) is None
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_security_guard_check.py -v`
Expected: 4 passed

**Step 3: Commit**

```bash
git add tests/hooks/test_security_guard_check.py
git commit -m "test: add security-guard-check.sh tests

Covers plugin conflict detection, missing settings, and malformed JSON."
```

---

### Task 4: test_wal_guard.py (parametrized port)

**Files:**
- Create: `tests/hooks/test_wal_guard.py`
- Reference: `hooks/wal-guard`, `tests/test_wal_guard.sh`

**Step 1: Write parametrized tests**

```python
"""Tests for wal-guard hook — dangerous bash command blocking."""
import json

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


# (label, command, expected_decision)
# "allow" = empty stdout, "deny" = JSON with permissionDecision=deny
GUARD_CASES = [
    # AC1-3: Local file/git/gh operations should ALLOW
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
    # AC4: Actual deployment commands should DENY
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
    # Existing destructive patterns
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


class TestWalGuard:
    @pytest.mark.parametrize("label,command,expected", GUARD_CASES, ids=[c[0] for c in GUARD_CASES])
    def test_guard_pattern(self, label, command, expected):
        stdin = {"tool_input": {"command": command}}
        stdout, stderr, rc = run_hook("wal-guard", stdin)
        assert rc == 0
        output = parse_hook_output(stdout)
        if expected == "allow":
            assert output is None, f"Expected allow but got deny: {stdout}"
        else:
            assert output is not None, f"Expected deny but got allow for: {command}"
            assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_empty_command_allowed(self):
        stdout, stderr, rc = run_hook("wal-guard", {"tool_input": {"command": ""}})
        assert rc == 0
        assert parse_hook_output(stdout) is None

    def test_missing_command_allowed(self):
        stdout, stderr, rc = run_hook("wal-guard", {"tool_input": {}})
        assert rc == 0
        assert parse_hook_output(stdout) is None

    def test_missing_jq_denies_all(self, no_jq_env):
        """wal-guard is FAIL-CLOSED: missing jq -> deny everything."""
        stdout, stderr, rc = run_hook(
            "wal-guard",
            {"tool_input": {"command": "echo hello"}},
            env_override=no_jq_env,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "jq" in output["hookSpecificOutput"].get("permissionDecisionReason", "").lower()
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_wal_guard.py -v`
Expected: 33+ passed

**Step 3: Add deprecation comment to old test**

Add to top of `tests/test_wal_guard.sh`:
```bash
# DEPRECATED: Superseded by tests/hooks/test_wal_guard.py (pytest)
# Remove this file once pytest version is validated in CI.
```

**Step 4: Commit**

```bash
git add tests/hooks/test_wal_guard.py tests/test_wal_guard.sh
git commit -m "test: port wal-guard tests to pytest with fail-closed jq test

Parametrized 30 existing cases + empty/missing command + jq-absent deny.
Marks tests/test_wal_guard.sh as deprecated."
```

---

### Task 5: test_wal_pre_post.py (WAL logging hooks)

**Files:**
- Create: `tests/hooks/test_wal_pre_post.py`
- Reference: `hooks/wal-pre`, `hooks/wal-post`, `hooks/wal-post-fail`, `hooks/wal-lib.sh`

**Step 1: Write tests**

```python
"""Tests for WAL logging hooks: wal-pre, wal-post, wal-post-fail."""
import json
from pathlib import Path

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


def _make_stdin(tool_name="Bash", session_id="test-sess", tool_use_id="tu-1",
                cwd=None, command="echo hello"):
    """Build standard hook stdin JSON."""
    d = {
        "tool_name": tool_name,
        "session_id": session_id,
        "tool_use_id": tool_use_id,
    }
    if cwd:
        d["cwd"] = str(cwd)
    if tool_name == "Bash":
        d["tool_input"] = {"command": command}
    elif tool_name == "Write":
        d["tool_input"] = {"file_path": "/tmp/test.txt", "content": "hello"}
    elif tool_name == "Edit":
        d["tool_input"] = {"file_path": "/tmp/test.txt", "old_string": "a", "new_string": "b"}
    return d


class TestWalPre:
    def test_writes_intent_entry(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdin = _make_stdin(cwd=ws.root)
        run_hook("wal-pre", stdin, cwd=ws.root)

        wal_file = ws.wal_dir / "testproj.jsonl"
        assert wal_file.exists()
        entry = json.loads(wal_file.read_text().strip())
        assert entry["phase"] == "INTENT"
        assert entry["session"] == "test-sess"
        assert entry["tool"] == "Bash"
        assert entry["tool_use_id"] == "tu-1"
        assert "ts" in entry
        assert "summary" in entry
        assert "cwd" in entry

    def test_intent_summary_bash(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdin = _make_stdin(session_id="s1", command="ls -la /tmp", cwd=ws.root)
        run_hook("wal-pre", stdin, cwd=ws.root)

        entry = json.loads((ws.wal_dir / "testproj.jsonl").read_text().strip())
        assert "ls -la /tmp" in entry["summary"]

    def test_intent_summary_write(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdin = _make_stdin(tool_name="Write", session_id="s1", cwd=ws.root)
        run_hook("wal-pre", stdin, cwd=ws.root)

        entry = json.loads((ws.wal_dir / "testproj.jsonl").read_text().strip())
        assert "write" in entry["summary"].lower()

    def test_unbound_session_writes_nothing(self, make_workspace):
        ws = make_workspace(registry_entries=[])
        stdin = _make_stdin(session_id="unbound-sess", cwd=ws.root)
        run_hook("wal-pre", stdin, cwd=ws.root)

        wal_file = ws.wal_dir / "testproj.jsonl"
        assert not wal_file.exists() or wal_file.read_text().strip() == ""

    def test_missing_jq_exits_silently(self, make_workspace, no_jq_env):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdin = _make_stdin(session_id="s1", cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-pre", stdin, cwd=ws.root, env_override=no_jq_env)
        assert rc == 0
        assert parse_hook_output(stdout) is None


class TestWalPost:
    def test_writes_done_entry(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdin = _make_stdin(session_id="s1", cwd=ws.root)
        run_hook("wal-post", stdin, cwd=ws.root)

        entry = json.loads((ws.wal_dir / "testproj.jsonl").read_text().strip())
        assert entry["phase"] == "DONE"
        assert entry["session"] == "s1"
        assert entry["tool"] == "Bash"


class TestWalPostFail:
    def test_writes_fail_entry(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdin = _make_stdin(session_id="s1", cwd=ws.root)
        run_hook("wal-post-fail", stdin, cwd=ws.root)

        entry = json.loads((ws.wal_dir / "testproj.jsonl").read_text().strip())
        assert entry["phase"] == "FAIL"
        assert entry["session"] == "s1"
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_wal_pre_post.py -v`
Expected: 7+ passed

**Step 3: Commit**

```bash
git add tests/hooks/test_wal_pre_post.py
git commit -m "test: add WAL pre/post/post-fail hook tests

Covers INTENT/DONE/FAIL entries, summary extraction, unbound skip, jq-absent."
```

---

### Task 6: test_wal_stop.py

**Files:**
- Create: `tests/hooks/test_wal_stop.py`
- Reference: `hooks/wal-stop`

**Step 1: Write tests**

```python
"""Tests for wal-stop hook — session end marker."""
import json

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


class TestWalStop:
    def test_writes_complete_marker(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        run_hook("wal-stop", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)

        notes = (ws.notes_dir / "testproj.md").read_text()
        assert "COMPLETE" in notes
        assert "s1" in notes

    def test_writes_stop_wal_entry(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        run_hook("wal-stop", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)

        wal_file = ws.wal_dir / "testproj.jsonl"
        assert wal_file.exists()
        entry = json.loads(wal_file.read_text().strip())
        assert entry["phase"] == "STOP"
        assert entry["session"] == "s1"

    def test_skips_duplicate_complete(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            session_notes={"testproj": "# Session Notes\n\n---\n# Session: 2026-03-08T00:00:00Z | ID: s1 | Status: COMPLETE\n"},
        )
        run_hook("wal-stop", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)

        notes = (ws.notes_dir / "testproj.md").read_text()
        assert notes.count("COMPLETE") == 1  # Not duplicated

    def test_registry_miss_falls_back_to_active(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True,
                 "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],  # No registry match
        )
        run_hook("wal-stop", {"session_id": "unknown-sess", "cwd": str(ws.root)}, cwd=ws.root)

        # Should fall back to first active project
        notes_file = ws.notes_dir / "alpha.md"
        assert notes_file.exists()
        assert "COMPLETE" in notes_file.read_text()

    def test_missing_workspace_exits_silently(self, tmp_path):
        stdout, stderr, rc = run_hook(
            "wal-stop",
            {"session_id": "s1", "cwd": str(tmp_path)},
            cwd=tmp_path,
        )
        assert rc == 0

    def test_unknown_session_id_exits_silently(self, make_workspace):
        ws = make_workspace(projects=[])  # No projects at all
        stdout, stderr, rc = run_hook(
            "wal-stop",
            {"session_id": "unknown", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_wal_stop.py -v`
Expected: 6 passed

**Step 3: Commit**

```bash
git add tests/hooks/test_wal_stop.py
git commit -m "test: add wal-stop hook tests

Covers COMPLETE marker, STOP WAL entry, duplicate skip, registry fallback."
```

---

### Task 7: test_wal_bind_guard.py

**Files:**
- Create: `tests/hooks/test_wal_bind_guard.py`
- Reference: `hooks/wal-bind-guard`

**Step 1: Write tests**

```python
"""Tests for wal-bind-guard — cross-project file write blocking."""
import json

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


def _make_stdin(tool_name, file_path, session_id="s1", cwd=None):
    """Build wal-bind-guard stdin."""
    d = {
        "tool_name": tool_name,
        "session_id": session_id,
    }
    if cwd:
        d["cwd"] = str(cwd)
    if tool_name == "NotebookEdit":
        d["tool_input"] = {"notebook_path": file_path}
    else:
        d["tool_input"] = {"file_path": file_path}
    return d


class TestBoundSession:
    def test_same_project_allows(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "beta", "path": "./projects/beta", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[{"session_id": "s1", "project": "alpha", "project_path": "./projects/alpha"}],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Write", file_path, cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root)
        assert rc == 0
        assert parse_hook_output(stdout) is None  # Allowed

    def test_cross_project_denies(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "beta", "path": "./projects/beta", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[{"session_id": "s1", "project": "alpha", "project_path": "./projects/alpha"}],
        )
        file_path = str(ws.root / "projects" / "beta" / "src" / "main.py")
        stdin = _make_stdin("Edit", file_path, cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "beta" in output["systemMessage"]

    def test_no_file_path_allows(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "alpha", "project_path": "./projects/alpha"}],
        )
        stdin = {"tool_name": "Edit", "session_id": "s1", "cwd": str(ws.root),
                 "tool_input": {}}
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root)
        assert rc == 0
        assert parse_hook_output(stdout) is None

    def test_relative_path_allows(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "alpha", "project_path": "./projects/alpha"}],
        )
        stdin = _make_stdin("Write", "relative/path.txt", cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root)
        assert rc == 0
        assert parse_hook_output(stdout) is None


class TestUnboundSession:
    def test_multi_active_project_file_denies(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "beta", "path": "./projects/beta", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],  # Unbound
        )
        file_path = str(ws.root / "projects" / "alpha" / "file.py")
        stdin = _make_stdin("Write", file_path, session_id="unbound", cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_multi_active_workspace_file_allows(self, make_workspace):
        """Workspace-level files must be writable for /rawgentic:switch to work."""
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "beta", "path": "./projects/beta", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],
        )
        file_path = str(ws.root / ".rawgentic_workspace.json")
        stdin = _make_stdin("Edit", file_path, session_id="unbound", cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root)
        assert rc == 0
        assert parse_hook_output(stdout) is None  # Allowed

    def test_single_active_allows(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "only", "path": "./projects/only", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],
        )
        file_path = str(ws.root / "projects" / "only" / "file.py")
        stdin = _make_stdin("Write", file_path, session_id="unbound", cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root)
        assert rc == 0
        assert parse_hook_output(stdout) is None


class TestFailOpen:
    def test_missing_jq_allows(self, make_workspace, no_jq_env):
        ws = make_workspace()
        file_path = str(ws.root / "projects" / "testproj" / "file.py")
        stdin = _make_stdin("Write", file_path, cwd=ws.root)
        stdout, stderr, rc = run_hook("wal-bind-guard", stdin, cwd=ws.root, env_override=no_jq_env)
        assert rc == 0
        # Fail-open: missing jq means allow
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_wal_bind_guard.py -v`
Expected: 8 passed

**Step 3: Commit**

```bash
git add tests/hooks/test_wal_bind_guard.py
git commit -m "test: add wal-bind-guard cross-project write tests

Covers bound/unbound sessions, workspace-level exception, fail-open jq."
```

---

### Task 8: test_wal_context.py

**Files:**
- Create: `tests/hooks/test_wal_context.py`
- Reference: `hooks/wal-context`

**Step 1: Write tests**

```python
"""Tests for wal-context — UserPromptSubmit context injection."""
import json

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


class TestBoundSession:
    def test_emits_context(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "testproj" in ctx
        assert "s1" in ctx

    def test_writes_current_session_id(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        run_hook("wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)

        sid_file = ws.root / "claude_docs" / ".current_session_id"
        assert sid_file.exists()
        assert sid_file.read_text() == "s1"


class TestUnboundSingleActive:
    def test_auto_binds_and_emits(self, make_workspace):
        ws = make_workspace(
            projects=[{"name": "solo", "path": "./projects/solo", "active": True,
                       "configured": True, "lastUsed": "2026-03-08T00:00:00Z"}],
            registry_entries=[],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "new-sess", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "solo" in ctx

        # Should have auto-registered in session registry
        registry = ws.registry.read_text()
        assert "new-sess" in registry
        assert "solo" in registry


class TestUnboundMultiActive:
    def test_emits_switch_prompt(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "beta", "path": "./projects/beta", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "new-sess", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "switch" in ctx.lower() or "Multiple" in ctx


class TestUnboundNoActive:
    def test_emits_no_projects_message(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "off", "path": "./projects/off", "active": False, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "new-sess", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "No active" in ctx or "no active" in ctx


class TestMissingWorkspace:
    def test_exits_silently(self, tmp_path):
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(tmp_path)},
            cwd=tmp_path,
        )
        assert rc == 0

    def test_unknown_session_exits_silently(self):
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "unknown", "cwd": "/nonexistent"},
        )
        assert rc == 0
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_wal_context.py -v`
Expected: 7 passed

**Step 3: Commit**

```bash
git add tests/hooks/test_wal_context.py
git commit -m "test: add wal-context hook tests

Covers bound/unbound/multi-active/no-active context injection, auto-bind."
```

---

### Task 9: test_wal_lib.py (bash function unit tests)

**Files:**
- Create: `tests/hooks/test_wal_lib.py`
- Reference: `hooks/wal-lib.sh`

**Step 1: Write tests**

```python
"""Unit tests for wal-lib.sh shared library functions.

Tests individual bash functions by sourcing wal-lib.sh in a subprocess
and echoing variable values after function calls.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
WAL_LIB = HOOKS_DIR / "wal-lib.sh"


def _run_bash(script, *, env_override=None, timeout=5):
    """Run a bash script fragment that sources wal-lib.sh."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


class TestWalParseInput:
    def _parse_and_echo(self, json_input, field):
        script = f"""
source "{WAL_LIB}"
export WAL_INPUT='{json.dumps(json_input)}'
WAL_TOOL_NAME=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_name // "unknown"')
WAL_SESSION_ID=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.session_id // "unknown"')
WAL_TOOL_USE_ID=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_use_id // "unknown"')
WAL_CWD=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.cwd // "."')
echo "${field}"
"""
        # Replace the echo with the actual variable
        script = script.replace('echo "${field}"', f'echo "${{{field}}}"')
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        return stdout

    def test_extracts_tool_name(self):
        result = self._parse_and_echo(
            {"tool_name": "Bash", "session_id": "s1", "tool_use_id": "tu1", "cwd": "/tmp"},
            "WAL_TOOL_NAME",
        )
        assert result == "Bash"

    def test_extracts_session_id(self):
        result = self._parse_and_echo(
            {"tool_name": "Bash", "session_id": "my-session", "tool_use_id": "tu1", "cwd": "/tmp"},
            "WAL_SESSION_ID",
        )
        assert result == "my-session"

    def test_defaults_missing_fields(self):
        result = self._parse_and_echo({}, "WAL_TOOL_NAME")
        assert result == "unknown"


class TestWalExtractSummary:
    def _extract(self, tool_name, tool_input):
        input_json = json.dumps({
            "tool_name": tool_name,
            "tool_input": tool_input,
            "session_id": "s1",
            "tool_use_id": "tu1",
            "cwd": "/tmp",
        })
        script = f"""
source "{WAL_LIB}"
WAL_INPUT='{input_json}'
WAL_TOOL_NAME="{tool_name}"
WAL_SESSION_ID="s1"
WAL_TOOL_USE_ID="tu1"
WAL_CWD="/tmp"
wal_extract_summary
echo "$WAL_SUMMARY"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        return stdout

    def test_bash_command(self):
        assert "ls -la" in self._extract("Bash", {"command": "ls -la /tmp"})

    def test_write_file(self):
        result = self._extract("Write", {"file_path": "/foo/bar.py"})
        assert "write" in result.lower()
        assert "bar.py" in result

    def test_edit_file(self):
        result = self._extract("Edit", {"file_path": "/foo/bar.py"})
        assert "edit" in result.lower()

    def test_task_description(self):
        result = self._extract("Task", {"description": "run linting"})
        assert "run linting" in result

    def test_unknown_tool(self):
        result = self._extract("Grep", {})
        assert result == "Grep"


class TestWalFindWorkspace:
    def test_finds_workspace_in_cwd(self, make_workspace):
        ws = make_workspace()
        script = f"""
source "{WAL_LIB}"
WAL_CWD="{ws.root}"
wal_find_workspace
echo "$WAL_WORKSPACE_FILE"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert ".rawgentic_workspace.json" in stdout

    def test_finds_workspace_in_parent(self, make_workspace):
        ws = make_workspace()
        child = ws.root / "projects" / "testproj" / "src"
        child.mkdir(parents=True, exist_ok=True)
        script = f"""
source "{WAL_LIB}"
WAL_CWD="{child}"
wal_find_workspace
echo "$WAL_WORKSPACE_FILE"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert ".rawgentic_workspace.json" in stdout

    def test_returns_failure_when_missing(self, tmp_path):
        script = f"""
source "{WAL_LIB}"
WAL_CWD="{tmp_path}"
wal_find_workspace
echo "exit:$?"
"""
        stdout, _, _ = _run_bash(script)
        assert "exit:1" in stdout
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_wal_lib.py -v`
Expected: 11 passed

**Step 3: Commit**

```bash
git add tests/hooks/test_wal_lib.py
git commit -m "test: add wal-lib.sh unit tests

Tests parse_input, extract_summary, find_workspace via bash subprocess."
```

---

### Task 10: test_session_start.py (most complex)

**Files:**
- Create: `tests/hooks/test_session_start.py`
- Reference: `hooks/session-start`

**Step 1: Write tests**

```python
"""Tests for session-start hook — WAL recovery, rotation, archival, context."""
import json
import os
from pathlib import Path

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


def _run_session_start(cwd, session_id="test-sess", event_type="startup", env_override=None):
    stdin = {
        "session_id": session_id,
        "cwd": str(cwd),
        "hook_event_name": event_type,
    }
    return run_hook("session-start", stdin, cwd=cwd, env_override=env_override)


class TestReconciliation:
    def test_deactivates_missing_project_dir(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "exists", "path": "./projects/exists", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "gone", "path": "./projects/gone", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            create_project_dirs=False,
        )
        # Only create the "exists" directory
        (ws.root / "projects" / "exists").mkdir(parents=True)

        _run_session_start(ws.root)

        updated = json.loads(ws.workspace_json.read_text())
        for p in updated["projects"]:
            if p["name"] == "gone":
                assert p["active"] is False
            if p["name"] == "exists":
                assert p["active"] is True

    def test_reconciliation_only_on_startup_resume(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "gone", "path": "./projects/gone", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            create_project_dirs=False,
        )
        # compact event should NOT trigger reconciliation
        _run_session_start(ws.root, event_type="compact")

        updated = json.loads(ws.workspace_json.read_text())
        assert updated["projects"][0]["active"] is True  # Not deactivated


class TestWalRecovery:
    def test_detects_incomplete_operations(self, make_workspace):
        wal_entries = [
            {"ts": "2026-03-08T00:00:00Z", "phase": "INTENT", "session": "old",
             "tool": "Bash", "tool_use_id": "orphan-1", "summary": "rm -rf /", "cwd": "/tmp"},
            {"ts": "2026-03-08T00:00:01Z", "phase": "INTENT", "session": "old",
             "tool": "Edit", "tool_use_id": "complete-1", "summary": "edit file", "cwd": "/tmp"},
            {"ts": "2026-03-08T00:00:02Z", "phase": "DONE", "session": "old",
             "tool": "Edit", "tool_use_id": "complete-1"},
        ]
        ws = make_workspace(wal_entries={"testproj": wal_entries},
                            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                                               "project_path": "./projects/testproj"}])

        stdout, stderr, rc = _run_session_start(ws.root)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "incomplete" in ctx.lower() or "WAL RECOVERY" in ctx
        assert "orphan-1" in ctx or "rm -rf" in ctx

    def test_no_incomplete_no_recovery_message(self, make_workspace):
        wal_entries = [
            {"ts": "2026-03-08T00:00:00Z", "phase": "INTENT", "session": "old",
             "tool": "Bash", "tool_use_id": "ok-1", "summary": "ls", "cwd": "/tmp"},
            {"ts": "2026-03-08T00:00:01Z", "phase": "DONE", "session": "old",
             "tool": "Bash", "tool_use_id": "ok-1"},
        ]
        ws = make_workspace(wal_entries={"testproj": wal_entries},
                            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                                               "project_path": "./projects/testproj"}])

        stdout, stderr, rc = _run_session_start(ws.root)
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "WAL RECOVERY" not in ctx


class TestArchival:
    def test_archives_large_session_notes(self, make_workspace):
        large_content = "# Notes\n" + ("x\n" * 700)
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        _run_session_start(ws.root, event_type="startup")

        archive_dir = ws.notes_dir / "archive"
        assert archive_dir.exists()
        archived = list(archive_dir.glob("testproj_*.md"))
        assert len(archived) == 1
        assert len(archived[0].read_text()) > 600

        # Original file should be reset
        current = (ws.notes_dir / "testproj.md").read_text()
        assert len(current.splitlines()) < 5

    def test_no_archival_on_compact_event(self, make_workspace):
        large_content = "# Notes\n" + ("x\n" * 700)
        ws = make_workspace(session_notes={"testproj": large_content})

        _run_session_start(ws.root, event_type="compact")

        archive_dir = ws.notes_dir / "archive"
        assert not archive_dir.exists()


class TestContextEmission:
    def test_emits_valid_json(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdout, stderr, rc = _run_session_start(ws.root)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        assert "hookSpecificOutput" in output
        assert "hookEventName" in output["hookSpecificOutput"]

    def test_writes_current_session_id(self, make_workspace):
        ws = make_workspace()
        _run_session_start(ws.root, session_id="my-session")

        sid_file = ws.root / "claude_docs" / ".current_session_id"
        assert sid_file.exists()
        assert sid_file.read_text() == "my-session"

    def test_no_workspace_emits_no_workspace_message(self, tmp_path):
        stdout, stderr, rc = _run_session_start(tmp_path)
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "No rawgentic workspace" in ctx or "new-project" in ctx
```

**Step 2: Run tests**

Run: `pytest tests/hooks/test_session_start.py -v`
Expected: 8 passed

**Step 3: Commit**

```bash
git add tests/hooks/test_session_start.py
git commit -m "test: add session-start hook tests

Covers reconciliation, WAL recovery, archival, and context emission."
```

---

### Task 11: Update docs/testing.md

**Files:**
- Modify: `docs/testing.md`

**Step 1: Rewrite docs/testing.md with complete test descriptions**

Replace the "Tests to Be Implemented" section with descriptions of all new test files. Add a "Skill Evaluation" section.

**Step 2: Commit**

```bash
git add docs/testing.md
git commit -m "docs: update testing.md with all hook test descriptions and skill eval methodology"
```

---

### Task 12: Add sync-security-patterns evals.json

**Files:**
- Create: `skills/sync-security-patterns-workspace/evals/evals.json`

**Step 1: Write evals.json matching skill-creator schema**

Reference `skills/sync-security-patterns/SKILL.md` for the skill's purpose, then create 2-3 test scenarios.

**Step 2: Commit**

```bash
git add skills/sync-security-patterns-workspace/evals/evals.json
git commit -m "test: add evals.json for sync-security-patterns skill"
```

---

### Task 13: Run Full Suite and Create PR

**Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: All tests pass (existing 78 + new ~80 = ~158 total)

**Step 2: Fix any failures (budget: 2 iterations)**

**Step 3: Push and create PR**

```bash
GH_TOKEN=<your-github-token> git push -u origin test/comprehensive-hook-suite
```

Then create PR via `gh pr create` with test results summary.
