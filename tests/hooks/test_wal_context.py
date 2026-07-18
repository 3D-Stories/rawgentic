"""Tests for wal-context — UserPromptSubmit context injection."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output, HOOKS_DIR


def _write_state(ws, project="testproj", *, workflow="wf2", step="8a",
                 step_title="Code review", issue=480, session_id="s1",
                 age_minutes=0):
    """Drop a step_state record (Task 1's shape) into claude_docs/wal/.

    age_minutes rewinds entered_at so a value > the read helper's 240-min
    default reads as stale.
    """
    entered = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
               ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {"schema_version": 1, "project": project, "workflow": workflow,
           "step": step, "step_title": step_title, "issue": issue,
           "session_id": session_id, "entered_at": entered}
    (ws.wal_dir / f"{project}.state.json").write_text(json.dumps(rec) + "\n")


# A notes file whose header carries "ID: s1" (so the grep path finds it) but no
# "Session: <ts>" token, so the age line stays empty and the grep-path output is
# deterministic across runs — lets the stale test byte-compare fallback output.
_NOTES_S1 = (
    "# Session Notes -- testproj\n"
    "### Session ID: s1\n"
    "## Task: Grep task\n"
)


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


class TestProtectionLevelInContext:
    def test_default_strict_in_header(self, make_workspace):
        """No .rawgentic.json → strict default appears in context header."""
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        output = parse_hook_output(stdout)
        ctx = output.get("additionalContext", "")
        assert "strict" in ctx

    def test_sandbox_level_in_header(self, make_workspace):
        """protectionLevel: sandbox appears in context header."""
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            project_configs={"testproj": {"protectionLevel": "sandbox"}},
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        output = parse_hook_output(stdout)
        ctx = output.get("additionalContext", "")
        assert "sandbox" in ctx

    def test_standard_level_in_header(self, make_workspace):
        """protectionLevel: standard appears in context header."""
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            project_configs={"testproj": {"protectionLevel": "standard"}},
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        output = parse_hook_output(stdout)
        ctx = output.get("additionalContext", "")
        assert "standard" in ctx


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


class TestStepStatePreferred:
    """#480 AC3 — a FRESH step-entry state file is the authoritative now-pointer:
    wal-context emits a Current-step line from it and suppresses the notes-grep
    Task/Status line; stale/absent/corrupt state falls through to the grep path
    byte-identically (fail-open)."""

    def _registry(self):
        return [{"session_id": "s1", "project": "testproj",
                 "project_path": "./projects/testproj"}]

    def test_fresh_state_emits_current_step_and_suppresses_grep_task(self, make_workspace):
        ws = make_workspace(
            registry_entries=self._registry(),
            session_notes={"testproj": _NOTES_S1},
        )
        _write_state(ws)  # fresh (age 0), workflow wf2 / step 8a / Code review / #480
        stdout, stderr, rc = run_hook(
            "wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)
        assert rc == 0
        ctx = parse_hook_output(stdout).get("additionalContext", "")
        assert "Current step: wf2 Step 8a (Code review)" in ctx
        assert "issue #480" in ctx
        assert "[state @" in ctx
        # the grep-derived Task/Status line is superseded, not duplicated
        assert "Grep task" not in ctx
        assert "Task:" not in ctx
        assert "Status:" not in ctx

    def test_fresh_state_null_issue_renders_dash(self, make_workspace):
        ws = make_workspace(registry_entries=self._registry())
        _write_state(ws, issue=None)
        stdout, stderr, rc = run_hook(
            "wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)
        assert rc == 0
        ctx = parse_hook_output(stdout).get("additionalContext", "")
        assert "Current step: wf2 Step 8a (Code review)" in ctx
        assert "issue #-" in ctx

    def test_stale_state_falls_through_to_grep_byte_identical(self, make_workspace):
        ws = make_workspace(
            registry_entries=self._registry(),
            session_notes={"testproj": _NOTES_S1},
        )
        _write_state(ws, age_minutes=600)  # 10h old > 240-min default -> stale
        stdout_stale, _, rc1 = run_hook(
            "wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)
        ctx_stale = parse_hook_output(stdout_stale).get("additionalContext", "")
        # remove the state file entirely -> the no-state-file case
        (ws.wal_dir / "testproj.state.json").unlink()
        stdout_none, _, rc2 = run_hook(
            "wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)
        ctx_none = parse_hook_output(stdout_none).get("additionalContext", "")
        assert rc1 == 0 and rc2 == 0
        assert "Current step:" not in ctx_stale
        assert "Task: Grep task" in ctx_stale
        # stale state must be byte-identical to having no state file at all
        assert ctx_stale == ctx_none

    def test_corrupt_state_falls_through_and_exits_0(self, make_workspace):
        ws = make_workspace(
            registry_entries=self._registry(),
            session_notes={"testproj": _NOTES_S1},
        )
        (ws.wal_dir / "testproj.state.json").write_text("{ not json\n")
        stdout, stderr, rc = run_hook(
            "wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)
        assert rc == 0
        ctx = parse_hook_output(stdout).get("additionalContext", "")
        assert "Current step:" not in ctx
        assert "Task: Grep task" in ctx

    def test_absent_state_no_current_step_line(self, make_workspace):
        """Zero behavior change when the state file is absent (AC3)."""
        ws = make_workspace(
            registry_entries=self._registry(),
            session_notes={"testproj": _NOTES_S1},
        )
        stdout, stderr, rc = run_hook(
            "wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)
        assert rc == 0
        ctx = parse_hook_output(stdout).get("additionalContext", "")
        assert "Current step:" not in ctx
        assert "Task: Grep task" in ctx


class TestStepStateFailOpenStructure:
    """The helper invocation must be fail-open: a missing python3 / crashing
    helper can never abort _do_context (which would emit NO context — a
    regression on the grep path). Simulating a missing python3 in the black-box
    harness is not clean (python3 and bash resolve from the same PATH dirs), so
    per the #480 design the fail-open guard is pinned structurally here."""

    def test_helper_invoked_via_script_dir_read_subcommand(self):
        text = (HOOKS_DIR / "wal-context").read_text(encoding="utf-8")
        assert 'step_state.py" read' in text, \
            "wal-context must invoke step_state.py's read subcommand"

    def test_helper_invocation_is_wrapped_fail_open(self):
        text = (HOOKS_DIR / "wal-context").read_text(encoding="utf-8")
        # anchor on the actual invocation (not the prose comment above it)
        idx = text.index('step_state.py" read')
        window = text[idx:idx + 300]
        assert "|| true" in window, \
            "step_state.py invocation is not wrapped in the fail-open '|| true' idiom"


class TestStepStateSessionScoping:
    """#480 Step-8a (converged R1+R2 Medium): AC2's session_id disambiguation must be REAL —
    a foreign session's fresh record must not masquerade as this session's position."""

    def test_foreign_session_state_falls_back_to_grep(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            session_notes={"testproj": _NOTES_S1},
        )
        _write_state(ws, session_id="OTHER-session")  # fresh but not ours
        stdout, _, rc = run_hook("wal-context", {"session_id": "s1", "cwd": str(ws.root)},
                                 cwd=ws.root)
        assert rc == 0
        ctx = parse_hook_output(stdout).get("additionalContext", "")
        assert "Current step:" not in ctx          # foreign record suppressed
        assert "Grep task" in ctx                  # byte-identical grep fallback

    def test_fresh_state_full_line_pinned(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            session_notes={"testproj": _NOTES_S1},
        )
        _write_state(ws)
        stdout, _, rc = run_hook("wal-context", {"session_id": "s1", "cwd": str(ws.root)},
                                 cwd=ws.root)
        assert rc == 0
        ctx = parse_hook_output(stdout).get("additionalContext", "")
        # the FULL rendered prefix as ONE substring (R1 Low: connective + em-dash pinned)
        assert "  Current step: wf2 Step 8a (Code review) — issue #480 [state @ " in ctx

    def test_newline_in_title_stays_single_line(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            session_notes={"testproj": _NOTES_S1},
        )
        _write_state(ws, step_title="Code\nreview")
        stdout, _, rc = run_hook("wal-context", {"session_id": "s1", "cwd": str(ws.root)},
                                 cwd=ws.root)
        assert rc == 0
        ctx = parse_hook_output(stdout).get("additionalContext", "")
        for line in ctx.splitlines():
            if "Current step:" in line:
                assert "[state @" in line  # title newline flattened; line intact
                break
        else:
            raise AssertionError("Current step line missing")
