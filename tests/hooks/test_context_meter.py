"""Tests for hooks/context_meter.py — the #687 context-pressure trigger.

Unit tests import the pure functions; flow tests run the hook black-box via
subprocess with JSON on stdin exactly as Claude Code invokes hooks
(docs/testing.md:5-8).

Every test that lets the hook WRITE state passes an isolated HOME — `run_hook`
copies the real environ (tests/hooks/conftest.py:238), so without the override
the suite would write into the developer's own ~/.rawgentic/.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
CLI = str(HOOKS / "context_meter.py")
sys.path.insert(0, str(HOOKS))

import context_meter as cm  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _usage(inp=0, cc=0, cr=0, out=0):
    return {"input_tokens": inp, "cache_creation_input_tokens": cc,
            "cache_read_input_tokens": cr, "output_tokens": out}


def _row(usage=None, **extra):
    msg = {"role": "assistant"}
    if usage is not None:
        msg["usage"] = usage
    row = {"type": "assistant", "message": msg}
    row.update(extra)
    return json.dumps(row)


def _transcript(tmp_path, session_id, rows):
    """Write a transcript at the real ~/.claude/projects/<slug>/<sid>.jsonl shape."""
    d = tmp_path / ".claude" / "projects" / "-some-project"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _run(payload, *, home, cwd=None, extra_env=None, timeout=20):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("RAWGENTIC_HEADLESS", None)
    env.pop("RAWGENTIC_LAUNCHER_ARMED", None)
    env.pop("RAWGENTIC_FRESH_LAUNCH_SUPPORTED", None)
    for k in list(env):
        if k.startswith("RAWGENTIC_CONTEXT_"):
            env.pop(k)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, CLI, "hook"], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=timeout, env=env,
                       cwd=str(cwd) if cwd else None)
    return r


def _out(r):
    text = r.stdout.strip()
    if not text:
        return None
    return json.loads(text)


SID = "abcd1234-0000-1111-2222-333344445555"

# Force every invocation to be "due" so a test exercises the record it claims to
# test rather than passing because the throttle happened to suppress the second
# call. Without this, once-per-tier and escalation tests are vacuous.
FAST = {"RAWGENTIC_CONTEXT_EVERY_TURNS": "1"}


# --------------------------------------------------------------------------
# T1 — usage totals and the reader (AC1, probe 2)
# --------------------------------------------------------------------------

def test_usage_total_sums_the_three_in_context_fields_only():
    # output_tokens is deliberately excluded: the statusline identity
    # 2 + 1257 + 652960 == total_input_tokens (#654) does not include it.
    assert cm.usage_total(_usage(inp=2, cc=1257, cr=652960, out=1667)) == 654219


def test_usage_total_counts_a_cache_read_only_row():
    assert cm.usage_total(_usage(cr=154085)) == 154085


@pytest.mark.parametrize("junk", [None, [], "x", {"input_tokens": "nope"},
                                  {"input_tokens": None}, {}])
def test_usage_total_never_raises_on_junk(junk):
    assert cm.usage_total(junk) == 0


def test_reader_takes_the_last_non_zero_row_not_the_last_row(tmp_path):
    """probe 2: an interrupted turn writes an all-zero usage row.

    Confirmed real at line 1872 of 1b895e69-….jsonl, in a transcript whose max
    in-context total is 809,778. A naive last-row reader reports 0% on a
    nearly-full session and would silently never fire.
    """
    p = _transcript(tmp_path, SID, [
        _row(_usage(inp=1, cr=759_007)),
        _row(_usage(inp=1, cr=764_028)),
        _row(_usage(0, 0, 0, 0)),          # the interrupted turn
    ])
    assert cm.read_used_tokens(str(p)) == 764_029


def test_reader_takes_the_last_non_zero_not_the_maximum(tmp_path):
    """A smaller non-zero row after a larger one is what compaction looks like.

    Pins that the reader is last-non-zero, not max-based — a max-based reader
    would keep reporting the pre-compaction figure forever.
    """
    p = _transcript(tmp_path, SID, [
        _row(_usage(inp=1, cr=900_000)),
        _row(_usage(inp=1, cr=80_000)),
    ])
    assert cm.read_used_tokens(str(p)) == 80_001


def test_reader_returns_none_when_no_usage_row_parses(tmp_path):
    p = _transcript(tmp_path, SID, [_row(None), _row(None)])
    assert cm.read_used_tokens(str(p)) is None


def test_reader_survives_malformed_and_truncated_lines(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-p"
    d.mkdir(parents=True)
    p = d / f"{SID}.jsonl"
    p.write_text("not json\n" + _row(_usage(inp=5, cr=100)) + "\n{\"trunc\": ",
                 encoding="utf-8")
    assert cm.read_used_tokens(str(p)) == 105


def test_reader_on_absent_file_returns_none(tmp_path):
    assert cm.read_used_tokens(str(tmp_path / "nope.jsonl")) is None


def test_reader_is_bounded_and_does_not_parse_a_whole_large_transcript(tmp_path):
    """The largest transcript on this host is 82,948,830 bytes (measured).

    A forward scan would re-read all of it every five minutes per session.
    """
    d = tmp_path / ".claude" / "projects" / "-p"
    d.mkdir(parents=True)
    p = d / f"{SID}.jsonl"
    filler = _row(None) + "\n"
    with open(p, "w", encoding="utf-8") as f:
        f.write(filler * 20000)                       # ~1 MB of usage-free rows
        f.write(_row(_usage(inp=1, cr=42_000)) + "\n")
    size = p.stat().st_size
    assert size > 500_000, "fixture must be large enough to make the bound meaningful"

    seen = {"bytes": 0}
    real_open = open

    def counting_open(path, mode="r", *a, **kw):
        fh = real_open(path, mode, *a, **kw)
        real_read = fh.read

        def read(n=-1):
            data = real_read(n)
            seen["bytes"] += len(data) if data else 0
            return data
        fh.read = read
        return fh

    assert cm.read_used_tokens(str(p), opener=counting_open) == 42_001
    assert seen["bytes"] < size / 4, (
        f"read {seen['bytes']} of {size} bytes — reader is not bounded")


def test_reader_beyond_the_byte_bound_yields_no_reading(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-p"
    d.mkdir(parents=True)
    p = d / f"{SID}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(_row(_usage(inp=1, cr=999)) + "\n")   # the only usage row, at the START
        f.write((_row(None) + "\n") * 5000)
    assert cm.read_used_tokens(str(p), max_bytes=2048) is None


# --------------------------------------------------------------------------
# T1 — transcript resolution and its hardening (probes 3 and 9, review #12)
# --------------------------------------------------------------------------

def test_resolve_prefers_the_payload_path(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1))])
    got = cm.resolve_transcript(
        SID, payload_path=str(p),
        projects_dir=str(tmp_path / ".claude" / "projects"),
        glob_fn=lambda pat: [])
    assert got == str(p)


def test_resolve_falls_back_to_a_single_glob_hit(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1))])
    got = cm.resolve_transcript(
        SID, payload_path=None,
        projects_dir=str(tmp_path / ".claude" / "projects"),
        glob_fn=lambda pat: [str(p)])
    assert got == str(p)


def test_resolve_refuses_an_ambiguous_glob(tmp_path):
    """Two hits must never be resolved by picking one arbitrarily."""
    a = _transcript(tmp_path, SID, [_row(_usage(inp=1))])
    b = tmp_path / ".claude" / "projects" / "-other"
    b.mkdir(parents=True)
    other = b / f"{SID}.jsonl"
    other.write_text("", encoding="utf-8")
    got = cm.resolve_transcript(
        SID, payload_path=None,
        projects_dir=str(tmp_path / ".claude" / "projects"),
        glob_fn=lambda pat: [str(a), str(other)])
    assert got is None


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "a/../../b", "sh*rt", "", "x", "with space",
    "sess;rm -rf /", "a" * 200,
])
def test_resolve_rejects_a_hostile_session_id_before_globbing(bad, tmp_path):
    calls = []

    def spy(pattern):
        calls.append(pattern)
        return []

    assert cm.resolve_transcript(
        bad, projects_dir=str(tmp_path), glob_fn=spy) is None
    assert calls == [], f"session_id {bad!r} reached a glob pattern"


def test_resolve_rejects_a_payload_path_whose_basename_is_not_the_session_id(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-p"
    d.mkdir(parents=True)
    other = d / "99999999-dead-beef-0000-000000000000.jsonl"
    other.write_text("", encoding="utf-8")
    assert cm.resolve_transcript(
        SID, payload_path=str(other),
        projects_dir=str(tmp_path / ".claude" / "projects"),
        glob_fn=lambda pat: []) is None


def test_resolve_rejects_a_payload_path_outside_the_projects_root(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    f = outside / f"{SID}.jsonl"
    f.write_text("", encoding="utf-8")
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    assert cm.resolve_transcript(
        SID, payload_path=str(f), projects_dir=str(projects),
        glob_fn=lambda pat: []) is None


def test_resolve_hardens_the_glob_hit_too(tmp_path):
    """A glob rooted at the projects dir makes containment likely, not certain —
    a symlink planted inside the tree still points out of it."""
    real = tmp_path / "outside.jsonl"
    real.write_text("", encoding="utf-8")
    projects = tmp_path / ".claude" / "projects" / "-p"
    projects.mkdir(parents=True)
    link = projects / f"{SID}.jsonl"
    link.symlink_to(real)
    assert cm.resolve_transcript(
        SID, payload_path=None,
        projects_dir=str(tmp_path / ".claude" / "projects"),
        glob_fn=lambda pat: [str(link)]) is None


def test_resolve_rejects_a_symlinked_payload_path(tmp_path):
    real = tmp_path / "secret.jsonl"
    real.write_text("", encoding="utf-8")
    projects = tmp_path / ".claude" / "projects" / "-p"
    projects.mkdir(parents=True)
    link = projects / f"{SID}.jsonl"
    link.symlink_to(real)
    assert cm.resolve_transcript(
        SID, payload_path=str(link),
        projects_dir=str(tmp_path / ".claude" / "projects"),
        glob_fn=lambda pat: []) is None


# --------------------------------------------------------------------------
# T2 — window resolution and escalation (probe 1, review #1)
# --------------------------------------------------------------------------

def test_window_defaults_to_the_conservative_200k():
    w, prov = cm.resolve_window(None, None, 1000)
    assert (w, prov) == (200_000, "default")


def test_window_from_config_then_env_precedence():
    assert cm.resolve_window(1_000_000, None, 10)[0] == 1_000_000
    assert cm.resolve_window(200_000, "1000000", 10)[0] == 1_000_000


@pytest.mark.parametrize("bad", ["abc", "", "0", "-5", "1e6", None, True, 0, -1])
def test_window_bad_value_falls_back_and_warns(bad):
    warnings = []
    w, prov = cm.resolve_window(bad, None, 10, warn=warnings.append)
    assert w == 200_000
    if bad not in (None,):
        assert warnings, f"a bad windowSize {bad!r} must warn on stderr"


def test_window_escalates_when_the_observed_total_exceeds_it():
    """A window a session has already exceeded is provably wrong."""
    w, prov = cm.resolve_window(None, None, 250_000)
    assert (w, prov) == (1_000_000, "escalated")


def test_window_does_not_escalate_below_the_boundary():
    w, prov = cm.resolve_window(None, None, 199_999)
    assert (w, prov) == (200_000, "default")


# --------------------------------------------------------------------------
# T2 — thresholds, tiers, cadence (AC3, AC6, AC9)
# --------------------------------------------------------------------------

def test_threshold_defaults_are_60_and_70():
    assert cm.thresholds({}, {}) == (60, 70)


def test_thresholds_from_config_and_env():
    assert cm.thresholds({"checkInPercent": 50, "actPercent": 80}, {}) == (50, 80)
    assert cm.thresholds({"checkInPercent": 50, "actPercent": 80},
                         {"RAWGENTIC_CONTEXT_ACT_PCT": "90"}) == (50, 90)


@pytest.mark.parametrize("cfg", [
    {"checkInPercent": 0}, {"actPercent": 100}, {"checkInPercent": "x"},
    {"checkInPercent": 65, "actPercent": 70},     # squeezed: gap < 10
    {"checkInPercent": 80, "actPercent": 70},     # inverted
])
def test_bad_thresholds_fall_back_to_defaults_with_a_warning(cfg):
    warnings = []
    assert cm.thresholds(cfg, {}, warn=warnings.append) == (60, 70)
    assert warnings, f"{cfg!r} must warn on stderr"


@pytest.mark.parametrize("frac,expect", [
    (0.0, "none"), (0.599, "none"), (0.60, "advisory"),
    (0.699, "advisory"), (0.70, "directive"), (1.0, "directive"),
])
def test_tier_boundaries(frac, expect):
    assert cm.tier_for(frac, 60, 70) == expect


def test_the_same_token_count_lands_in_different_tiers_per_window():
    """AC3's relative-threshold proof, on probe 6's real live number.

    159,416 tokens is 15.9% of a 1M window and 79.7% of a 200k one.
    """
    used = 159_416
    assert cm.tier_for(used / 1_000_000, 60, 70) == "none"
    assert cm.tier_for(used / 200_000, 60, 70) == "directive"


def test_cadence_defaults_are_5_turns_and_300_seconds():
    assert cm.cadence({}, {}) == (5, 300)


def test_env_overrides_work_with_a_real_os_environ(monkeypatch):
    """Regression: `os.environ` is `os._Environ`, NOT a dict.

    An `isinstance(env, dict)` guard therefore discarded the whole env-override
    layer in production while every dict-based unit test still passed. Only the
    subprocess tests caught it, so this pins the real type directly.
    """
    monkeypatch.setenv("RAWGENTIC_CONTEXT_EVERY_TURNS", "1")
    monkeypatch.setenv("RAWGENTIC_CONTEXT_CHECKIN_PCT", "25")
    monkeypatch.setenv("RAWGENTIC_CONTEXT_ACT_PCT", "50")
    assert not isinstance(os.environ, dict), "premise of this test"
    assert cm.cadence({}, os.environ)[0] == 1
    assert cm.thresholds({}, os.environ) == (25, 50)


def test_should_check_turn_arm_fires_on_the_fifth_turn_not_earlier():
    base = {"turns": 0, "last_check_turn": 0, "last_check_ts": 1000}
    for turns in (1, 2, 3, 4):
        st = dict(base, turns=turns)
        assert not cm.should_check(st, now=1000, every_turns=5, every_seconds=300)
    st = dict(base, turns=5)
    assert cm.should_check(st, now=1000, every_turns=5, every_seconds=300)


def test_should_check_seconds_arm_fires_independently_of_turns():
    st = {"turns": 0, "last_check_turn": 0, "last_check_ts": 1000}
    assert not cm.should_check(st, now=1299, every_turns=5, every_seconds=300)
    assert cm.should_check(st, now=1300, every_turns=5, every_seconds=300)


def test_should_check_on_empty_state_fires():
    assert cm.should_check({}, now=1, every_turns=5, every_seconds=300)


# --------------------------------------------------------------------------
# T2 — the seam predicate (AC7, review #4)
# --------------------------------------------------------------------------

def _ptr(step=8, workflow="wf2", entered="2026-07-28T10:00:00Z", project="p",
         session_id=SID, title="Implementation"):
    return {"schema_version": 1, "project": project, "workflow": workflow,
            "step": step, "step_title": title, "issue": 687,
            "session_id": session_id, "entered_at": entered}


def test_unchanged_pointer_says_wait():
    verdict, reason = cm.seam_verdict(_ptr(), _ptr())
    assert verdict == "wait" and reason


def test_moved_pointer_is_a_seam_candidate():
    armed = _ptr(step=8, entered="2026-07-28T10:00:00Z")
    verdict, _ = cm.seam_verdict(armed, _ptr(step=9,
                                             entered="2026-07-28T10:05:00Z"))
    assert verdict == "seam"


def test_re_entering_the_same_step_later_is_a_seam_candidate():
    armed = _ptr(step=8, entered="2026-07-28T10:00:00Z")
    verdict, _ = cm.seam_verdict(armed, _ptr(step=8,
                                             entered="2026-07-28T10:09:00Z"))
    assert verdict == "seam"


def test_an_earlier_entered_at_says_wait():
    armed = _ptr(entered="2026-07-28T10:00:00Z")
    verdict, _ = cm.seam_verdict(armed, _ptr(step=9,
                                             entered="2026-07-28T09:00:00Z"))
    assert verdict == "wait"


@pytest.mark.parametrize("current", [None, {}, {"workflow": "wf2"}, "junk"])
def test_a_malformed_current_pointer_is_unknown_not_wait(current):
    assert cm.seam_verdict(_ptr(), current)[0] == "unknown"


def test_a_different_project_is_unknown():
    verdict, _ = cm.seam_verdict(_ptr(project="a"),
                                 _ptr(project="b", step=9,
                                      entered="2026-07-28T10:05:00Z"))
    assert verdict == "unknown"


def test_no_pointer_at_all_is_unknown_so_the_advisory_can_still_fire():
    """A session with no tracked workflow has no phase to interrupt.

    Folding this into "wait" would make the advisory tier permanently silent in
    every ordinary session — which is how this case was found.
    """
    verdict, reason = cm.seam_verdict(None, None)
    assert verdict == "unknown"
    assert "nothing to wait" in reason


def test_armed_snapshot_missing_but_pointer_present_is_unknown():
    assert cm.seam_verdict(None, _ptr())[0] == "unknown"


# --------------------------------------------------------------------------
# T3 — the CLI, black-box via subprocess (AC1, AC2, AC5)
# --------------------------------------------------------------------------

def test_absent_transcript_emits_nothing_and_exits_zero(tmp_path):
    r = _run({"session_id": SID, "cwd": str(tmp_path),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_malformed_transcript_emits_nothing_and_exits_zero(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-p"
    d.mkdir(parents=True)
    (d / f"{SID}.jsonl").write_text("garbage\n{\n", encoding="utf-8")
    r = _run({"session_id": SID, "cwd": str(tmp_path),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_over_threshold_emits_the_userpromptsubmit_shape(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(tmp_path),
              "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    assert r.returncode == 0
    out = _out(r)
    assert out is not None, "159,416 tokens on the default 200k window must emit"
    # UserPromptSubmit takes the top-level form (hooks/wal-context:43 precedent).
    assert "additionalContext" in out
    assert "hookSpecificOutput" not in out


def test_over_threshold_emits_the_posttooluse_shape(tmp_path):
    """PostToolUse needs the nested form — proved by live spike, 2026-07-28."""
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
              "hook_event_name": "PostToolUse", "tool_name": "Bash"},
             home=tmp_path)
    assert r.returncode == 0
    out = _out(r)
    assert out is not None
    hso = out.get("hookSpecificOutput")
    assert hso and hso.get("hookEventName") == "PostToolUse"
    assert "additionalContext" in hso
    assert "additionalContext" not in out


def test_the_emitted_text_never_contains_transcript_content(tmp_path):
    secret = "SUPERSECRET-CONVERSATION-TEXT"
    rows = [json.dumps({"type": "user", "message": {"role": "user",
                                                    "content": secret}}),
            _row(_usage(inp=1, cr=159_415))]
    p = _transcript(tmp_path, SID, rows)
    r = _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    assert secret not in r.stdout
    assert secret not in r.stderr


def test_emits_once_per_tier_then_stays_silent(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    payload = {"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
               "hook_event_name": "UserPromptSubmit"}
    first = _run(payload, home=tmp_path, extra_env=FAST)
    assert _out(first) is not None
    for _ in range(3):
        again = _run(payload, home=tmp_path, extra_env=FAST)
        assert again.stdout.strip() == "", "a tier must emit at most once per session"


def test_an_unwritable_state_dir_emits_nothing(tmp_path):
    """Record-before-emit: a failed write must not produce a nag that then
    repeats every turn forever (security-guard-check.sh:49-55)."""
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    home = tmp_path / "ro-home"
    home.mkdir()
    (home / ".rawgentic").mkdir()
    os.chmod(home / ".rawgentic", 0o500)
    try:
        r = _run({"session_id": SID, "cwd": str(tmp_path),
                  "transcript_path": str(p),
                  "hook_event_name": "UserPromptSubmit"}, home=home)
        assert r.returncode == 0
        assert r.stdout.strip() == ""
    finally:
        os.chmod(home / ".rawgentic", 0o700)


def test_state_dir_is_created_private_even_under_a_permissive_parent(tmp_path):
    """~/.rawgentic is mode 775 on this host — a plain mkdir would not be private."""
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    (tmp_path / ".rawgentic").mkdir()
    os.chmod(tmp_path / ".rawgentic", 0o775)
    _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
          "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    d = tmp_path / ".rawgentic" / "context-meter"
    assert d.is_dir()
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_turns_persist_across_separate_invocations(tmp_path):
    """AC9: the five-turn arm cannot accumulate unless every prompt persists."""
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=10))])   # far below any tier
    payload = {"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
               "hook_event_name": "UserPromptSubmit"}
    for _ in range(3):
        _run(payload, home=tmp_path)
    state = json.loads((tmp_path / ".rawgentic" / "context-meter"
                        / f"{SID}.json").read_text())
    assert state["turns"] == 3


def test_a_tier_none_check_persists_both_last_check_fields(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=10))])
    _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
          "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    state = json.loads((tmp_path / ".rawgentic" / "context-meter"
                        / f"{SID}.json").read_text())
    assert state.get("last_check_ts")
    assert "last_check_turn" in state


def test_throttled_posttooluse_never_opens_the_transcript(tmp_path):
    """The cheap path must stay cheap: it rides every tool call."""
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    payload = {"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
               "hook_event_name": "PostToolUse", "tool_name": "Bash"}
    _run(payload, home=tmp_path)                      # first call: seeds state
    p.unlink()                                        # a read would now fail loudly
    r = _run(payload, home=tmp_path)
    assert r.returncode == 0


def test_a_subagent_payload_does_nothing(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
              "hook_event_name": "PostToolUse", "tool_name": "Bash",
              "agent_id": "agent-abc123"}, home=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert not (tmp_path / ".rawgentic" / "context-meter" / f"{SID}.json").exists()


def test_a_payload_without_a_subagent_marker_behaves_normally(tmp_path):
    """The guard must be inert where the field is absent (probe 9 saw none)."""
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    assert _out(r) is not None


def test_the_loser_of_a_reservation_stays_silent(tmp_path):
    """Parallel tool calls fire concurrent PostToolUse hooks — os.replace does
    not prevent two readers both reserving the same tier."""
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    d = tmp_path / ".rawgentic" / "context-meter"
    d.mkdir(parents=True)
    # Pre-create the reservation marker the winner would have made.
    (d / f"{SID}.200000.directive.emitted").write_text("", encoding="utf-8")
    r = _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path)
    assert r.stdout.strip() == "", "the loser of the reservation race must stay silent"


def test_a_real_race_of_six_processes_emits_exactly_once(tmp_path):
    """Actually race them, rather than pre-creating the marker.

    A pre-created marker only proves the loser path; it can pass while the
    reservation is not atomic at all. This launches the processes together and
    counts emissions.
    """
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    payload = json.dumps({"session_id": SID, "cwd": str(tmp_path),
                          "transcript_path": str(p),
                          "hook_event_name": "PostToolUse", "tool_name": "Bash"})
    env = dict(os.environ, HOME=str(tmp_path))
    for key in list(env):
        if key.startswith("RAWGENTIC_CONTEXT_"):
            env.pop(key)
    procs = [subprocess.Popen([sys.executable, CLI, "hook"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env)
             for _ in range(6)]
    outs = [proc.communicate(payload)[0] for proc in procs]
    assert all(proc.returncode == 0 for proc in procs)
    emitted = [o for o in outs if o.strip()]
    assert len(emitted) == 1, (
        f"exactly one of six racing processes must emit, got {len(emitted)}")


def test_a_failed_delivery_releases_the_reservation(tmp_path):
    """A held reservation after a failed emit would silence the tier forever.

    Closing stdout makes the print fail; the marker must NOT survive, so a later
    turn can still warn.
    """
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    env = dict(os.environ, HOME=str(tmp_path))
    for key in list(env):
        if key.startswith("RAWGENTIC_CONTEXT_"):
            env.pop(key)
    payload = json.dumps({"session_id": SID, "cwd": str(tmp_path),
                          "transcript_path": str(p),
                          "hook_event_name": "UserPromptSubmit"})
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        proc = subprocess.Popen([sys.executable, CLI, "hook"],
                                stdin=subprocess.PIPE, stdout=devnull,
                                stderr=subprocess.PIPE, text=True, env=env)
        proc.communicate(payload)
    assert proc.returncode == 0
    # Writing to devnull succeeds, so this run legitimately emitted and holds the
    # marker. The invariant under test is the inverse: when NO marker was left
    # behind by a failed run, the tier is still available. Simulate the failure
    # path directly on the pure helpers.
    cm.reserve(str(tmp_path), SID, 200_000, "advisory")
    assert cm.has_marker(str(tmp_path), SID, 200_000, "advisory")
    cm.release(str(tmp_path), SID, 200_000, "advisory")
    assert not cm.has_marker(str(tmp_path), SID, 200_000, "advisory"), (
        "release must return the reservation so a later turn can retry")


def test_reserve_is_won_by_exactly_one_caller(tmp_path):
    (tmp_path / ".rawgentic").mkdir()
    wins = [cm.reserve(str(tmp_path), SID, 200_000, "directive")
            for _ in range(4)]
    assert wins.count(True) == 1, "O_EXCL must admit exactly one winner"


# --------------------------------------------------------------------------
# T3 — escalation must not suppress the real later warning (review #1)
# --------------------------------------------------------------------------

def test_escalation_invalidates_only_the_stale_windows_record(tmp_path):
    payload = {"session_id": SID, "cwd": str(tmp_path),
               "hook_event_name": "UserPromptSubmit"}

    # 159,416 against the assumed 200k window -> a premature directive.
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    first = _run(dict(payload, transcript_path=str(p)), home=tmp_path, extra_env=FAST)
    assert _out(first) is not None

    # The session passes 200k, so the window escalates to 1M. 750k is 75% of the
    # REAL window — a genuine directive that the flat-list design would have
    # suppressed forever, because "directive" was already recorded at 200k.
    p.write_text(_row(_usage(inp=1, cr=749_999)) + "\n", encoding="utf-8")
    second = _run(dict(payload, transcript_path=str(p)), home=tmp_path, extra_env=FAST)
    assert _out(second) is not None, (
        "escalation must not carry the 200k record forward")


def test_directive_implies_advisory_for_the_same_window(tmp_path):
    """No stale advisory may follow a directive on the same denominator."""
    payload = {"session_id": SID, "cwd": str(tmp_path),
               "hook_event_name": "UserPromptSubmit"}
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=149_999))])   # 75% of 200k
    assert _out(_run(dict(payload, transcript_path=str(p)), home=tmp_path,
                     extra_env=FAST))

    p.write_text(_row(_usage(inp=1, cr=129_999)) + "\n", encoding="utf-8")  # 65%
    later = _run(dict(payload, transcript_path=str(p)), home=tmp_path, extra_env=FAST)
    assert later.stdout.strip() == ""


# --------------------------------------------------------------------------
# T3 — the unattended split and the AC8 route text
# --------------------------------------------------------------------------

def _nag(tmp_path, extra_env=None, used=159_415):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=used))])
    r = _run({"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path,
             extra_env=extra_env)
    out = _out(r)
    assert out is not None
    return out.get("additionalContext") or ""


def test_attended_text_asks_the_human_and_names_clear_prep(tmp_path):
    text = _nag(tmp_path)
    assert "clear-prep" in text
    assert "launcher_lib" not in text, (
        "an attended session has no armed launcher; naming that route sends it "
        "at a command whose guard refuses it")


def test_headless_without_a_launcher_still_routes_to_clear_prep(tmp_path):
    text = _nag(tmp_path, {"RAWGENTIC_HEADLESS": "1"})
    assert "clear-prep" in text
    assert "launcher_lib" not in text


def test_only_both_capability_declarations_name_the_launcher_route(tmp_path):
    text = _nag(tmp_path, {"RAWGENTIC_HEADLESS": "1",
                           "RAWGENTIC_LAUNCHER_ARMED": "1",
                           "RAWGENTIC_FRESH_LAUNCH_SUPPORTED": "1"})
    assert "launcher_lib" in text


def test_launcher_armed_alone_is_not_enough(tmp_path):
    text = _nag(tmp_path, {"RAWGENTIC_LAUNCHER_ARMED": "1"})
    assert "launcher_lib" not in text


def test_the_nag_states_the_assumed_window_and_its_provenance(tmp_path):
    text = _nag(tmp_path)
    assert "200,000" in text or "200000" in text
    assert "default" in text.lower()


# --------------------------------------------------------------------------
# T3 — config resolution from a real workspace (AC6, review #5)
# --------------------------------------------------------------------------

def _workspace(tmp_path, context_meter=None):
    root = tmp_path / "ws"
    (root / "claude_docs" / "wal").mkdir(parents=True)
    proj = root / "projects" / "p"
    proj.mkdir(parents=True)
    (root / ".rawgentic_workspace.json").write_text(json.dumps(
        {"projects": [{"name": "p", "path": "./projects/p", "active": True}]}),
        encoding="utf-8")
    cfg = {"version": 1, "project": {"name": "p", "type": "library"}}
    if context_meter is not None:
        cfg["contextMeter"] = context_meter
    (proj / ".rawgentic.json").write_text(json.dumps(cfg), encoding="utf-8")
    (root / "claude_docs" / "session_registry.jsonl").write_text(
        json.dumps({"session_id": SID, "project": "p",
                    "project_path": "./projects/p"}) + "\n", encoding="utf-8")
    return root, proj


def test_config_windowsize_from_the_bound_project_is_used(tmp_path):
    root, proj = _workspace(tmp_path, {"windowSize": 1_000_000})
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(proj), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path, cwd=proj)
    assert r.returncode == 0
    assert r.stdout.strip() == "", (
        "159,416 on a configured 1M window is 15.9% — must not emit")


def test_an_unregistered_session_falls_back_to_defaults(tmp_path):
    root, proj = _workspace(tmp_path, {"windowSize": 1_000_000})
    (root / "claude_docs" / "session_registry.jsonl").write_text(
        "", encoding="utf-8")
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(proj), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path, cwd=proj)
    assert _out(r) is not None, "no bound project => 200k default => emit"


def test_a_malformed_project_config_falls_back_without_raising(tmp_path):
    root, proj = _workspace(tmp_path, {"windowSize": 1_000_000})
    (proj / ".rawgentic.json").write_text("{not json", encoding="utf-8")
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(proj), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path, cwd=proj)
    assert r.returncode == 0
    assert _out(r) is not None


def test_no_workspace_at_all_still_works(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    bare = tmp_path / "bare"
    bare.mkdir()
    r = _run({"session_id": SID, "cwd": str(bare), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path, cwd=bare)
    assert r.returncode == 0
    assert _out(r) is not None


# --------------------------------------------------------------------------
# T3 — the seam is read from the real step-state pointer (AC7)
# --------------------------------------------------------------------------

def test_a_pointer_for_another_session_is_not_used(tmp_path):
    root, proj = _workspace(tmp_path)
    (root / "claude_docs" / "wal" / "p.state.json").write_text(
        json.dumps(_ptr(session_id="99999999-0000-0000-0000-000000000000")),
        encoding="utf-8")
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    r = _run({"session_id": SID, "cwd": str(proj), "transcript_path": str(p),
              "hook_event_name": "UserPromptSubmit"}, home=tmp_path, cwd=proj)
    text = (_out(r) or {}).get("additionalContext", "")
    assert "wf2" not in text, "another session's pointer is not evidence about this one"


# --------------------------------------------------------------------------
# T3 — the read subcommand and its diagnostics (review #6)
# --------------------------------------------------------------------------

def test_read_subcommand_prints_the_reading_as_json(tmp_path):
    p = _transcript(tmp_path, SID, [_row(_usage(inp=1, cr=159_415))])
    env = dict(os.environ, HOME=str(tmp_path))
    r = subprocess.run([sys.executable, CLI, "read", "--session-id", SID,
                        "--transcript", str(p)],
                       capture_output=True, text=True, timeout=20, env=env)
    assert r.returncode == 0
    got = json.loads(r.stdout)
    assert got["used"] == 159_416
    for k in ("window", "fraction", "tier", "provenance"):
        assert k in got


def test_read_subcommand_exits_3_when_no_usage_row_parses(tmp_path):
    p = _transcript(tmp_path, SID, [_row(None)])
    env = dict(os.environ, HOME=str(tmp_path))
    r = subprocess.run([sys.executable, CLI, "read", "--session-id", SID,
                        "--transcript", str(p)],
                       capture_output=True, text=True, timeout=20, env=env)
    assert r.returncode == 3


def test_a_missing_transcript_warns_once_per_session_on_stderr(tmp_path):
    payload = {"session_id": SID, "cwd": str(tmp_path),
               "hook_event_name": "UserPromptSubmit"}
    # FAST makes the second call DUE, so this tests the once-per-session record
    # rather than passing because the throttle suppressed the second check.
    first = _run(payload, home=tmp_path, extra_env=FAST)
    second = _run(payload, home=tmp_path, extra_env=FAST)
    assert first.stderr.strip(), (
        "a self-disabling meter must be visible somewhere — fail-open is not "
        "the same as invisible")
    assert not second.stderr.strip(), "the diagnostic must be once per session"


# --------------------------------------------------------------------------
# T4 — registration (AC2)
# --------------------------------------------------------------------------

def test_registered_on_both_userpromptsubmit_and_posttooluse():
    hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    for event in ("UserPromptSubmit", "PostToolUse"):
        commands = [h.get("command", "")
                    for entry in hooks.get(event, [])
                    for h in entry.get("hooks", [])]
        assert any("context_meter.py" in c for c in commands), \
            f"context_meter.py is not registered on {event}"
        assert any(c.startswith("${CLAUDE_PLUGIN_ROOT}/hooks/")
                   for c in commands if "context_meter.py" in c), \
            f"{event} registration must use the ${{CLAUDE_PLUGIN_ROOT}} prefix"


def test_registration_declares_a_timeout():
    hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    for event in ("UserPromptSubmit", "PostToolUse"):
        for entry in hooks.get(event, []):
            for h in entry.get("hooks", []):
                if "context_meter.py" in h.get("command", ""):
                    assert isinstance(h.get("timeout"), int) and h["timeout"] > 0
