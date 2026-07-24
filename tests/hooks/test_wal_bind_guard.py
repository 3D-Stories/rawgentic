"""Tests for hooks/wal-bind-guard — PreToolUse guard enforcing session binding.

Covers:
- Bound session: same-project allow, cross-project deny, edge cases
- Unbound session: multi-active deny, workspace-file exception, single-active allow
- Fail-open: missing jq allows operation
"""
import json
from pathlib import Path

import pytest

from tests.hooks.conftest import Workspace, parse_hook_output, run_hook

HOOK = "wal-bind-guard"

# Reusable project definitions for multi-project workspaces
ALPHA_PROJECT = {
    "name": "alpha",
    "path": "./projects/alpha",
    "active": True,
    "configured": True,
    "lastUsed": "2026-03-08T00:00:00Z",
}
BETA_PROJECT = {
    "name": "beta",
    "path": "./projects/beta",
    "active": True,
    "configured": True,
    "lastUsed": "2026-03-08T00:00:00Z",
}


def _make_stdin(
    tool_name: str,
    session_id: str,
    cwd: str,
    tool_input: dict | None = None,
) -> dict:
    """Build the stdin payload for wal-bind-guard."""
    return {
        "tool_name": tool_name,
        "session_id": session_id,
        "cwd": cwd,
        "tool_input": tool_input or {},
    }


def _decision(stdout: str) -> str:
    """Return 'allow' or 'deny' from hook stdout."""
    parsed = parse_hook_output(stdout)
    if parsed is None:
        return "allow"
    perm = parsed.get("hookSpecificOutput", {}).get("permissionDecision", "")
    return "deny" if perm == "deny" else "allow"


# ── Bound session tests ──────────────────────────────────────────────────


class TestBoundSession:
    """Tests for sessions already bound to a project in the registry."""

    def test_same_project_allows(self, make_workspace) -> None:
        """Bound to alpha, editing a file in alpha -> allow."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"

    def test_cross_project_denies(self, make_workspace) -> None:
        """Bound to alpha, editing a file in beta -> deny mentioning beta."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        stdin = _make_stdin("Edit", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "deny"
        parsed = parse_hook_output(stdout)
        msg = parsed["systemMessage"]
        assert "beta" in msg.lower(), f"Deny message should mention beta: {msg}"

    def test_no_file_path_allows(self, make_workspace) -> None:
        """Empty tool_input (no file_path) -> allow."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        stdin = _make_stdin("Write", "s1", str(ws.root), {})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"

    def test_relative_path_allows(self, make_workspace) -> None:
        """Relative path 'foo/bar.txt' -> allow (not absolute, skipped)."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": "foo/bar.txt"})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"


# ── Gate-2 deny-JSON escaping (#325, mirror of Gate-1 #318) ──────────────────


class TestGate2DenyEscaping:
    """#325: the Gate-2 cross-project deny must emit well-formed JSON regardless of
    JSON-significant characters in the violating project's name. Before the fix the
    deny was built with a raw heredoc interpolating the name unescaped, so a `"` or
    `\\` malformed the JSON, the runtime dropped the deny, and the cross-project write
    silently fail-opened. VIOLATION is read raw from the workspace .name field and is
    never run through wal_validate_project_name, so a crafted/hand-edited name reaches
    the response builder.
    """

    @pytest.mark.parametrize(
        "bad_name",
        ['a"b', "a\\zb"],
        ids=["quote-in-name", "backslash-in-name"],
    )
    def test_cross_project_deny_json_wellformed(self, make_workspace, bad_name) -> None:
        """Bound to alpha; a cross-project write into a project whose name carries a
        `"` (or `\\`) must still parse as a valid deny — not fail-open."""
        bad_project = {
            "name": bad_name,
            "path": "./projects/aquote",
            "active": True,
            "configured": True,
            "lastUsed": "2026-03-08T00:00:00Z",
        }
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, bad_project],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        file_path = str(ws.root / "projects" / "aquote" / "lib" / "utils.py")
        stdin = _make_stdin("Edit", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        parsed = parse_hook_output(stdout)
        assert parsed is not None, f"deny output must be valid JSON, got: {stdout!r}"
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        # The (now-escaped) name still appears in the message — behaviour preserved.
        assert bad_name in parsed["systemMessage"]


# ── Unbound session tests ────────────────────────────────────────────────


class TestUnboundSession:
    """Tests for sessions not yet bound to any project."""

    def test_multi_active_project_file_denies(self, make_workspace) -> None:
        """Unbound, 2 active projects, file in alpha -> deny."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "deny"

    def test_multi_active_deny_message_names_tool_and_path(self, make_workspace) -> None:
        """#318 AC3/AC4: the Gate-1 deny message must name the denied tool AND the
        file path, so a wedged bootstrap (new-project register-existing reading a
        candidate project's files while unbound in a multi-active workspace) is
        diagnosable from the transcript — the observed symptom was three opaque
        denials that named neither."""
        ws: Workspace = make_workspace(projects=[ALPHA_PROJECT, BETA_PROJECT])
        file_path = str(ws.root / "projects" / "beta" / ".rawgentic.json")
        stdin = _make_stdin("Read", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "deny"
        msg = parse_hook_output(stdout)["systemMessage"]
        assert "Read" in msg, f"deny message must name the denied tool: {msg}"
        assert file_path in msg, f"deny message must name the file path: {msg}"

    def test_multi_active_deny_no_file_path_still_valid(self, make_workspace) -> None:
        """#318 regression: a Gate-1 deny with no file_path omits the path clause
        but still emits a well-formed deny that names the tool — the `[ -n
        "$FILE_PATH" ]` branch must not produce malformed JSON. MultiEdit is not
        in the workspace-level carve-out, so an empty tool_input reaches the deny
        with FILE_PATH unset (a Read with no path is allowed as a non-project file)."""
        ws: Workspace = make_workspace(projects=[ALPHA_PROJECT, BETA_PROJECT])
        stdin = _make_stdin("MultiEdit", "s1", str(ws.root), {})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        parsed = parse_hook_output(stdout)
        assert parsed is not None, f"deny must be valid JSON: {stdout!r}"
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "MultiEdit" in parsed["systemMessage"]

    def test_multi_active_workspace_file_allows(self, make_workspace) -> None:
        """Unbound, 2 active projects, file is .rawgentic_workspace.json -> allow."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
        )
        file_path = str(ws.root / ".rawgentic_workspace.json")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"

    def test_single_active_allows(self, make_workspace) -> None:
        """Unbound, only 1 active project -> allow (wal-context handles auto-bind)."""
        ws: Workspace = make_workspace(
            projects=[
                {
                    "name": "alpha",
                    "path": "./projects/alpha",
                    "active": True,
                    "configured": True,
                    "lastUsed": "2026-03-08T00:00:00Z",
                },
            ],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"


# ── Executor-dispatch env fallback (#640) ────────────────────────────────


class TestExecutorDispatchEnvFallback:
    """#640: an executor-dispatched subprocess (hooks/executor_routing_lib.py
    dispatch -> phase_executor's claude_cli adapter) is a fresh, unregistered
    `claude --print --no-session-persistence` session — it can never appear in
    session_registry.jsonl, so Gate 1 denies it in any workspace with >1 active
    project. The dispatch sets RAWGENTIC_DISPATCH_PROJECT to the project it was
    invoked for; wal-bind-guard accepts it as a fallback bound-project ONLY when
    it names a real ACTIVE project, and Gate 2's cross-project check still
    applies using that name.
    """

    def test_env_var_binds_unregistered_session_same_project_allows(
        self, make_workspace
    ) -> None:
        ws: Workspace = make_workspace(projects=[ALPHA_PROJECT, BETA_PROJECT])
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Read", "unregistered-s9", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(
            HOOK, stdin, cwd=ws.root, env_override={"RAWGENTIC_DISPATCH_PROJECT": "alpha"}
        )
        assert rc == 0
        assert _decision(stdout) == "allow"

    def test_env_var_still_denies_cross_project(self, make_workspace) -> None:
        """Bound via the env fallback to alpha; touching beta still denies —
        the fallback must feed Gate 2, not bypass it."""
        ws: Workspace = make_workspace(projects=[ALPHA_PROJECT, BETA_PROJECT])
        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        stdin = _make_stdin("Read", "unregistered-s9", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(
            HOOK, stdin, cwd=ws.root, env_override={"RAWGENTIC_DISPATCH_PROJECT": "alpha"}
        )
        assert rc == 0
        assert _decision(stdout) == "deny"

    def test_env_var_naming_nonexistent_project_denies(self, make_workspace) -> None:
        ws: Workspace = make_workspace(projects=[ALPHA_PROJECT, BETA_PROJECT])
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Read", "unregistered-s9", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(
            HOOK, stdin, cwd=ws.root, env_override={"RAWGENTIC_DISPATCH_PROJECT": "nope"}
        )
        assert rc == 0
        assert _decision(stdout) == "deny"

    def test_env_var_naming_inactive_project_denies(self, make_workspace) -> None:
        """A project name that exists but is active:false must not be trusted —
        the fallback validates active:true, same bar as a real registry bind.
        Isolated against an ACTIVE project's file (alpha), not the inactive
        project's own file: an inactive project's OWN files fall through Gate
        1's separate "not under any active project" exception regardless of
        BOUND_PROJECT (a pre-existing quirk, tracked separately, out of scope
        here — see #640 PR notes) — that would validate this test even if the
        env fallback wrongly bound to the inactive name, so it can't isolate
        this behavior. Pointing at alpha's file (a real active project) means
        an incorrect bind to "gamma" would wrongly ALLOW; the correct
        behavior (BOUND_PROJECT stays empty since gamma isn't active) DENIES
        via the normal unbound-in-multi-active path, same as no env var at
        all."""
        inactive = {
            "name": "gamma", "path": "./projects/gamma", "active": False,
            "configured": True, "lastUsed": "2026-03-08T00:00:00Z",
        }
        ws: Workspace = make_workspace(projects=[ALPHA_PROJECT, BETA_PROJECT, inactive])
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Read", "unregistered-s9", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(
            HOOK, stdin, cwd=ws.root, env_override={"RAWGENTIC_DISPATCH_PROJECT": "gamma"}
        )
        assert rc == 0
        assert _decision(stdout) == "deny"

    def test_registry_binding_takes_precedence_over_env_var(self, make_workspace) -> None:
        """A session ALREADY bound via the registry must not be re-bound by a
        (possibly stale/conflicting) env var — the env fallback only fires
        when the registry lookup is empty."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        stdin = _make_stdin("Read", "s1", str(ws.root), {"file_path": file_path})
        # Env var claims beta, but the registry already bound this exact session to alpha —
        # the registry must win, so a beta file still denies.
        stdout, _stderr, rc = run_hook(
            HOOK, stdin, cwd=ws.root, env_override={"RAWGENTIC_DISPATCH_PROJECT": "beta"}
        )
        assert rc == 0
        assert _decision(stdout) == "deny"


# ── Fail-open tests ─────────────────────────────────────────────────────


class TestFailOpen:
    """Verify fail-open when jq is unavailable."""

    def test_missing_jq_allows(self, make_workspace, tmp_path) -> None:
        """Without jq, wal-bind-guard should fail-open (exit 0, no output).

        Since jq and bash typically share /usr/bin, the no_jq_env fixture
        strips bash too.  We build a shadow /usr/bin with all binaries
        except jq, mirroring the approach in test_wal_guard.py.
        """
        import os as _os
        import shutil
        import subprocess as _sp

        from tests.hooks.conftest import HOOKS_DIR

        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
        )

        # Build shadow bin directory: symlink everything in /usr/bin except jq
        shadow = tmp_path / "shadow_bin"
        shadow.mkdir()
        for entry in Path("/usr/bin").iterdir():
            if entry.name == "jq":
                continue
            target = shadow / entry.name
            try:
                target.symlink_to(entry)
            except OSError:
                pass

        # Use a fake HOME so ~/.local/bin/jq is not found
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        # Build PATH: replace /usr/bin with shadow, drop any dir containing jq
        original_path = _os.environ.get("PATH", "")
        new_dirs: list[str] = []
        for d in original_path.split(_os.pathsep):
            if not d:
                continue
            if _os.path.normpath(d) == "/usr/bin":
                new_dirs.append(str(shadow))
            elif _os.path.isfile(_os.path.join(d, "jq")):
                continue
            else:
                new_dirs.append(d)

        env = dict(_os.environ)
        env["PATH"] = _os.pathsep.join(new_dirs)
        env["HOME"] = str(fake_home)

        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        stdin = _make_stdin("Edit", "s1", str(ws.root), {"file_path": file_path})

        hook_path = HOOKS_DIR / HOOK
        result = _sp.run(
            [str(shadow / "bash"), str(hook_path)],
            input=json.dumps(stdin),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=str(ws.root),
        )

        assert result.returncode == 0, f"Expected rc=0 (fail-open), got rc={result.returncode}"
        assert _decision(result.stdout) == "allow"


# ── #49: cross-project allowlist (crossProjectAllowedPaths) ──────────────────


class TestCrossProjectAllowlist:
    """Gate-2 opt-in allowlist relaxing SPECIFIC paths in other active projects."""

    def _bound(self, make_workspace, allowed):
        return make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
            workspace_fields=({"crossProjectAllowedPaths": allowed}
                              if allowed is not None else None),
        )

    def test_allows_docs_write(self, make_workspace) -> None:
        ws = self._bound(make_workspace, ["docs/**", "CLAUDE.md"])
        fp = str(ws.root / "projects" / "beta" / "docs" / "findings.md")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp})
        stdout, stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "allow"
        assert "crossProjectAllowedPaths" in stderr and "write" in stderr

    def test_allows_docs_read(self, make_workspace) -> None:
        ws = self._bound(make_workspace, ["docs/**"])
        fp = str(ws.root / "projects" / "beta" / "docs" / "x.md")
        stdin = _make_stdin("Read", "s1", str(ws.root), {"file_path": fp})
        stdout, stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "allow"
        assert "read" in stderr

    def test_denies_non_docs_write(self, make_workspace) -> None:
        ws = self._bound(make_workspace, ["docs/**"])
        fp = str(ws.root / "projects" / "beta" / "src" / "main.py")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "deny"

    def test_missing_config_default_deny(self, make_workspace) -> None:
        ws = self._bound(make_workspace, None)  # no crossProjectAllowedPaths
        fp = str(ws.root / "projects" / "beta" / "docs" / "x.md")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "deny"

    def test_exact_file_pattern(self, make_workspace) -> None:
        ws = self._bound(make_workspace, ["CLAUDE.md"])
        # exact match allowed
        fp_ok = str(ws.root / "projects" / "beta" / "CLAUDE.md")
        s_ok = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp_ok})
        out_ok, _e, rc_ok = run_hook(HOOK, s_ok, cwd=ws.root)
        assert rc_ok == 0 and _decision(out_ok) == "allow"
        # nested CLAUDE.md NOT matched by the bare pattern
        fp_no = str(ws.root / "projects" / "beta" / "docs" / "CLAUDE.md")
        s_no = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp_no})
        out_no, _e2, rc_no = run_hook(HOOK, s_no, cwd=ws.root)
        assert rc_no == 0 and _decision(out_no) == "deny"

    def test_no_false_prefix_match(self, make_workspace) -> None:
        # AC8: "docs/**" must not match "docs-extra/…"
        ws = self._bound(make_workspace, ["docs/**"])
        fp = str(ws.root / "projects" / "beta" / "docs-extra" / "x.md")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "deny"

    def test_symlink_traversal_blocked(self, make_workspace, tmp_path) -> None:
        # AC4: a symlinked docs/ escaping the target dir must NOT be allowed by the list
        ws = self._bound(make_workspace, ["docs/**"])
        outside = tmp_path / "outside_secret"
        outside.mkdir(parents=True, exist_ok=True)
        beta_docs = ws.root / "projects" / "beta" / "docs"
        beta_docs.parent.mkdir(parents=True, exist_ok=True)
        beta_docs.symlink_to(outside, target_is_directory=True)  # beta/docs -> outside
        fp = str(beta_docs / "escaped.md")  # resolves to outside/escaped.md
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "deny"

    def test_dotdot_traversal_blocked(self, make_workspace) -> None:
        # AC4: a plain ../ escape (no symlink) must not be allowed by the list
        ws = self._bound(make_workspace, ["docs/**"])
        fp = str(ws.root / "projects" / "beta" / "docs" / ".." / ".." / ".." / "escaped.md")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": fp})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "deny"

    def test_edit_tool_allowed(self, make_workspace) -> None:
        # AC2: Edit (not just Write/Read) is relaxed by the allowlist
        ws = self._bound(make_workspace, ["docs/**"])
        fp = str(ws.root / "projects" / "beta" / "docs" / "notes.md")
        stdin = _make_stdin("Edit", "s1", str(ws.root), {"file_path": fp})
        stdout, stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "allow"
        assert "write" in stderr

    def test_notebookedit_tool_allowed(self, make_workspace) -> None:
        # AC2: NotebookEdit shares the same relaxed path (reads notebook_path, not file_path)
        ws = self._bound(make_workspace, ["docs/**"])
        fp = str(ws.root / "projects" / "beta" / "docs" / "nb.ipynb")
        stdin = _make_stdin("NotebookEdit", "s1", str(ws.root), {"notebook_path": fp})
        stdout, stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0 and _decision(stdout) == "allow"
        assert "crossProjectAllowedPaths" in stderr  # non-vacuous: allowed by list, not empty-path


class TestBoundProjectFastPath:
    """#268: bound-session jq cost drops 6 -> 5 — the registry's two field
    reads are ONE jq, and the own-project check + violation scan are ONE jq
    (own-path derived from the workspace entry for the validated bound name,
    never from the registry's unvalidated project_path)."""

    def _run_counting(self, ws, tmp_path, file_path):
        import os as _os
        import shutil as _shutil
        import subprocess as sp
        from tests.hooks.conftest import HOOKS_DIR
        real_jq = _shutil.which("jq")
        assert real_jq
        count_file = tmp_path / "jq-count"
        shim_dir = tmp_path / "shimbin"
        shim_dir.mkdir()
        shim = shim_dir / "jq"
        shim.write_text(
            f'#!/usr/bin/env bash\necho x >> "{count_file}"\n'
            f'exec "{real_jq}" "$@"\n'
        )
        shim.chmod(0o755)
        env = dict(_os.environ)
        # HOME -> tmp so _resolve_jq finds no ~/.local/bin/jq and falls back
        # to PATH, where the counting shim sits first.
        env["HOME"] = str(tmp_path)
        env["PATH"] = f"{shim_dir}{_os.pathsep}{env['PATH']}"
        payload = json.dumps({
            "tool_name": "Read", "session_id": "s1",
            "tool_use_id": "tu-fp", "cwd": str(ws.root),
            "tool_input": {"file_path": file_path},
        })
        result = sp.run(
            ["bash", str(HOOKS_DIR / HOOK)],
            input=payload, capture_output=True, text=True,
            timeout=10, cwd=str(ws.root), env=env,
        )
        spawns = (
            len(count_file.read_text().splitlines())
            if count_file.exists()
            else 0
        )
        return result, spawns

    def test_bound_project_read_skips_violation_scan(
        self, make_workspace, tmp_path
    ) -> None:
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[{
                "session_id": "s1", "project": "alpha",
                "project_path": "./projects/alpha",
                "ts": "2026-03-08T00:00:00Z",
            }],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        result, spawns = self._run_counting(ws, tmp_path, file_path)
        assert result.returncode == 0, result.stderr
        assert _decision(result.stdout) == "allow"
        assert spawns == 5, (
            f"bound-project Read spawned jq {spawns} times, want 5 "
            f"(stdin parse, claude_docs, combined registry read, file-path, "
            f"combined own-check+violation scan)"
        )

    def test_cross_project_still_denies_with_project_path(
        self, make_workspace, tmp_path
    ) -> None:
        """The fast path must not weaken Gate 2: bound to alpha WITH a
        project_path in the registry, touching beta still denies."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[{
                "session_id": "s1", "project": "alpha",
                "project_path": "./projects/alpha",
                "ts": "2026-03-08T00:00:00Z",
            }],
        )
        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        result, _spawns = self._run_counting(ws, tmp_path, file_path)
        assert result.returncode == 0, result.stderr
        assert _decision(result.stdout) == "deny"

    def test_inconsistent_registry_project_path_still_denies(
        self, make_workspace, tmp_path
    ) -> None:
        """#268 Step 11 R2 catch: the fast path must derive its prefix from
        the WORKSPACE entry for the (validated) bound name — never from the
        registry's unvalidated project_path. A stale or hand-edited entry
        (project alpha, project_path pointing at beta) must not fast-path
        allow beta's files."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[{
                "session_id": "s1", "project": "alpha",
                "project_path": "./projects/beta",
                "ts": "2026-03-08T00:00:00Z",
            }],
        )
        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        result, _spawns = self._run_counting(ws, tmp_path, file_path)
        assert result.returncode == 0, result.stderr
        assert _decision(result.stdout) == "deny", (
            "inconsistent registry project_path must not defeat the "
            "cross-project deny"
        )
