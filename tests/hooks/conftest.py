"""Shared fixtures and helpers for hook integration tests.

Provides:
- Workspace dataclass for consistent path references
- make_workspace factory fixture to scaffold test workspace directories
- run_hook() to invoke hook scripts as subprocesses (importable standalone)
- parse_hook_output() to parse JSON from hook stdout
- no_jq_env fixture to simulate environments without jq
"""
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# Absolute path to the hooks/ directory, resolved relative to this file.
HOOKS_DIR = (Path(__file__).resolve().parent.parent.parent / "hooks")


@dataclass
class Workspace:
    """Paths within a scaffolded test workspace."""

    root: Path
    workspace_json: Path
    registry: Path
    wal_dir: Path
    notes_dir: Path


@pytest.fixture()
def make_workspace(tmp_path: Path):
    """Factory fixture that builds a fake rawgentic workspace on disk.

    Parameters
    ----------
    projects : list[dict]
        Project entries for .rawgentic_workspace.json.
        Default: single active project "testproj" at "./projects/testproj".
    registry_entries : list[dict]
        Lines to write to claude_docs/session_registry.jsonl.
    wal_entries : dict[str, list[dict]]
        Mapping of project name -> list of WAL JSON objects.
        Each list is written to claude_docs/wal/{name}.jsonl.
    session_notes : dict[str, str]
        Mapping of project name -> markdown content.
        Written to claude_docs/session_notes/{name}.md.
    create_project_dirs : bool
        Whether to create the project directories on disk (default True).

    Returns
    -------
    Workspace
        Dataclass with resolved paths into the created workspace.
    """

    def _factory(
        *,
        projects: list[dict[str, Any]] | None = None,
        registry_entries: list[dict[str, Any]] | None = None,
        wal_entries: dict[str, list[dict[str, Any]]] | None = None,
        session_notes: dict[str, str] | None = None,
        create_project_dirs: bool = True,
    ) -> Workspace:
        root = tmp_path

        # -- Default project list --
        if projects is None:
            projects = [
                {
                    "name": "testproj",
                    "path": "./projects/testproj",
                    "active": True,
                    "lastUsed": "2026-01-01T00:00:00Z",
                    "configured": True,
                }
            ]

        # -- .rawgentic_workspace.json --
        workspace_json = root / ".rawgentic_workspace.json"
        workspace_json.write_text(
            json.dumps(
                {"version": 1, "projectsDir": "./projects", "projects": projects},
                indent=2,
            )
        )

        # -- claude_docs directory tree --
        claude_docs = root / "claude_docs"
        claude_docs.mkdir(parents=True, exist_ok=True)

        # session_registry.jsonl
        registry = claude_docs / "session_registry.jsonl"
        if registry_entries:
            registry.write_text(
                "\n".join(json.dumps(e) for e in registry_entries) + "\n"
            )
        else:
            registry.touch()

        # wal/ directory + per-project WAL files
        wal_dir = claude_docs / "wal"
        wal_dir.mkdir(parents=True, exist_ok=True)

        if wal_entries:
            for proj_name, entries in wal_entries.items():
                wal_file = wal_dir / f"{proj_name}.jsonl"
                wal_file.write_text(
                    "\n".join(json.dumps(e) for e in entries) + "\n"
                )

        # session_notes/ directory + per-project notes
        notes_dir = claude_docs / "session_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        if session_notes:
            for proj_name, content in session_notes.items():
                notes_file = notes_dir / f"{proj_name}.md"
                notes_file.write_text(content)

        # -- Optionally create project directories --
        if create_project_dirs:
            for proj in projects:
                proj_path = root / proj["path"]
                proj_path.mkdir(parents=True, exist_ok=True)

        return Workspace(
            root=root,
            workspace_json=workspace_json,
            registry=registry,
            wal_dir=wal_dir,
            notes_dir=notes_dir,
        )

    return _factory


def run_hook(
    hook_name: str,
    stdin_dict: dict[str, Any],
    *,
    cwd: Path | None = None,
    env_override: dict[str, str] | None = None,
    timeout: int = 10,
) -> tuple[str, str, int]:
    """Run a hook script as a subprocess, feeding it JSON on stdin.

    Parameters
    ----------
    hook_name : str
        Filename of the hook inside HOOKS_DIR (e.g. "wal-guard", "security-guard.py").
    stdin_dict : dict
        JSON payload to send on stdin.  If ``cwd`` is provided and not already
        present in *stdin_dict*, it is injected automatically.
    cwd : Path, optional
        Working directory for the subprocess.  Also injected into stdin_dict
        if missing.
    env_override : dict, optional
        Extra environment variables merged on top of ``os.environ``.
    timeout : int
        Subprocess timeout in seconds (default 10).

    Returns
    -------
    tuple[str, str, int]
        (stdout, stderr, returncode)
    """
    hook_path = HOOKS_DIR / hook_name

    # Inject cwd into stdin payload if not already present
    payload = dict(stdin_dict)
    if cwd is not None and "cwd" not in payload:
        payload["cwd"] = str(cwd)

    # Determine interpreter from file extension
    suffix = hook_path.suffix
    if suffix == ".py":
        cmd = ["python3", str(hook_path)]
    elif suffix == ".sh":
        cmd = ["bash", str(hook_path)]
    else:
        # Hooks without extension (e.g. wal-guard, session-start) — use bash
        cmd = ["bash", str(hook_path)]

    # Build environment
    env = dict(os.environ)
    if env_override:
        env.update(env_override)

    result = subprocess.run(
        cmd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(cwd) if cwd else None,
    )

    return result.stdout, result.stderr, result.returncode


def parse_hook_output(stdout: str) -> dict[str, Any] | None:
    """Parse JSON from hook stdout.

    Returns the parsed dict, or None if stdout is empty or not valid JSON.
    """
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


@pytest.fixture()
def no_jq_env() -> dict[str, str]:
    """Return an environment dict with jq removed from PATH.

    Strips any PATH directory that contains a ``jq`` binary, and also
    removes ``~/.local/bin`` (hooks check there explicitly).
    """
    original_path = os.environ.get("PATH", "")
    home = os.environ.get("HOME", "")
    local_bin = os.path.join(home, ".local", "bin") if home else ""

    filtered_dirs: list[str] = []
    for d in original_path.split(os.pathsep):
        if not d:
            continue
        # Strip ~/.local/bin unconditionally
        if local_bin and os.path.normpath(d) == os.path.normpath(local_bin):
            continue
        # Strip any directory that contains a jq executable
        if os.path.isfile(os.path.join(d, "jq")):
            continue
        filtered_dirs.append(d)

    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(filtered_dirs)
    return env
