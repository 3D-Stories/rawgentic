"""Supervision decision telemetry — the append-only measurement surface (#963 AC5).

The store exists because #871 shipped a whole authority-and-claims core whose real-world
usage nobody could measure: a 2026-08-06 trace found ZERO live callers, which is what
killed the executor (D174). Every authority decision and claim transition now leaves a
line, so "is this machinery reached?" is answered by data instead of by reading code.

Two properties carry this module, and each has its own test below:

1. **Appends never interleave or truncate.** `flock` + `O_APPEND` + one `write()`, the
   `decision_log.append_record` pattern, verified here with concurrent writers.
2. **The caller owns the fail mode.** `append_event` RAISES; it never decides for the
   caller whether a missing line should stop an outward action. The broker's
   pre-execution appends are fail-closed, the post-execution ones are not.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import supervision_telemetry as st  # noqa: E402


def _lines(root):
    path = Path(st.telemetry_path(str(root)))
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _authority(**kw):
    event = {"kind": "authority", "action": "merge", "decision": "permitted",
             "reason": "attended", "campaign": "epic-963"}
    event.update(kw)
    return event


# ------------------------------------------------------------------ placement

def test_the_store_sits_beside_the_supervision_state(tmp_path):
    """Under `claude_docs/`, so it is a sibling of `session_notes/` rather than inside
    it — the `decision_log` durability argument: session-start trims
    `session_notes/*.md`, and a store the trimmer can reach is not durable."""
    assert st.telemetry_path(str(tmp_path)) == os.path.join(
        str(tmp_path), "claude_docs", "supervision-telemetry.jsonl")


def test_the_store_is_created_on_first_append_with_owner_only_mode(tmp_path):
    st.append_event(str(tmp_path), _authority())
    path = Path(st.telemetry_path(str(tmp_path)))
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"


# --------------------------------------------------------------- line content

def test_every_line_carries_a_schema_version_and_timestamp(tmp_path):
    st.append_event(str(tmp_path), _authority())
    line = _lines(tmp_path)[0]
    assert line["schema_version"] == st.SCHEMA_VERSION
    assert line["ts"].endswith("Z")
    assert line["kind"] == "authority"


def test_a_caller_supplied_ts_is_kept(tmp_path):
    st.append_event(str(tmp_path), _authority(ts="2026-08-06T12:00:00Z"))
    assert _lines(tmp_path)[0]["ts"] == "2026-08-06T12:00:00Z"


def test_the_session_id_is_captured_from_the_environment(tmp_path, monkeypatch):
    """So a line can be attributed to the run that wrote it, across sessions."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-abc")
    st.append_event(str(tmp_path), _authority())
    assert _lines(tmp_path)[0]["session"] == "sess-abc"


def test_a_missing_session_id_is_null_never_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    st.append_event(str(tmp_path), _authority())
    assert _lines(tmp_path)[0]["session"] is None


def test_claim_transitions_record_their_vocabulary(tmp_path):
    for transition in st.TRANSITIONS:
        st.append_event(str(tmp_path), {
            "kind": "claim", "transition": transition, "claim_id": "c-1",
            "action": "merge", "campaign": "epic-963"})
    assert [x["transition"] for x in _lines(tmp_path)] == list(st.TRANSITIONS)


def test_the_appender_never_invents_or_drops_caller_fields(tmp_path):
    st.append_event(str(tmp_path), _authority(issue=963, pr=970, repo="o/r",
                                              revision=4, load_status="valid"))
    line = _lines(tmp_path)[0]
    for key, value in (("issue", 963), ("pr", 970), ("repo", "o/r"),
                       ("revision", 4), ("load_status", "valid")):
        assert line[key] == value


# ------------------------------------------------------------------- appending

def test_appends_accumulate_and_never_rewrite(tmp_path):
    for i in range(5):
        st.append_event(str(tmp_path), _authority(reason=f"r{i}"))
    assert [x["reason"] for x in _lines(tmp_path)] == [f"r{i}" for i in range(5)]


def test_a_torn_previous_record_is_closed_before_appending(tmp_path):
    """A writer that died mid-record leaves no trailing newline. Appending straight
    onto it would JOIN the two and destroy both."""
    path = Path(st.telemetry_path(str(tmp_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"kind": "claim", "torn')
    st.append_event(str(tmp_path), _authority(reason="after-the-tear"))
    text = path.read_text()
    assert text.count("\n") == 2                     # tear closed, record added
    # The torn line stays unparseable — it is skipped, never repaired into a lie.
    events = st.read_events(str(tmp_path))
    assert [e["reason"] for e in events] == ["after-the-tear"]


def test_concurrent_writers_never_lose_or_interleave_a_line(tmp_path):
    """The reason this is not `atomic_write_text`: a whole-file replace would let the
    second writer silently drop the first record."""
    script = (
        f"import sys; sys.path.insert(0, {str(HOOKS)!r});\n"
        "import supervision_telemetry as st\n"
        "for i in range(40):\n"
        f"    st.append_event({str(tmp_path)!r}, "
        "{'kind': 'claim', 'transition': 'minted', 'claim_id': sys.argv[1] + str(i)})\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, tag])
             for tag in ("a", "b", "c")]
    for p in procs:
        assert p.wait(timeout=60) == 0
    lines = _lines(tmp_path)
    assert len(lines) == 120
    assert len({x["claim_id"] for x in lines}) == 120


# ------------------------------------------------------------------ fail modes

def test_append_raises_so_the_caller_owns_the_fail_mode(tmp_path):
    """The broker aborts before a merge on this; `cancel_claims` logs and continues.
    Deciding here would take that choice away from both."""
    path = Path(st.telemetry_path(str(tmp_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()                                     # a directory where the file goes
    with pytest.raises(OSError):
        st.append_event(str(tmp_path), _authority())


def test_a_non_dict_event_is_refused(tmp_path):
    for bad in ("not-an-event", ["a"], None, 7):
        with pytest.raises((TypeError, ValueError)):
            st.append_event(str(tmp_path), bad)


def test_an_event_without_a_kind_is_refused(tmp_path):
    """`kind` discriminates which of decision/transition a reader should expect."""
    with pytest.raises(ValueError):
        st.append_event(str(tmp_path), {"action": "merge"})


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ValueError):
        st.append_event(str(tmp_path), {"kind": "made-up", "action": "merge"})


def test_no_workspace_root_is_refused_rather_than_written_somewhere_odd(tmp_path):
    for empty in (None, "", 7):
        with pytest.raises((TypeError, ValueError)):
            st.append_event(empty, _authority())


# ------------------------------------------------------------------- the reader

def test_read_events_is_tolerant_of_a_corrupt_line(tmp_path):
    st.append_event(str(tmp_path), _authority(reason="first"))
    path = Path(st.telemetry_path(str(tmp_path)))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    st.append_event(str(tmp_path), _authority(reason="third"))
    events = st.read_events(str(tmp_path))
    assert [e["reason"] for e in events] == ["first", "third"]


def test_read_events_on_a_missing_store_is_empty_not_an_error(tmp_path):
    assert st.read_events(str(tmp_path)) == []


# ------------------------------------------------------- import-graph guard

def test_supervision_telemetry_imports_stdlib_only():
    """It is imported from `supervision_claims`, which sits under a per-tool-call read
    path. A heavy import here would leak into that path silently."""
    import ast
    tree = ast.parse((HOOKS / "supervision_telemetry.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {"datetime", "fcntl", "json", "os", "sys", "typing", "__future__"}
    assert imported <= allowed, f"non-stdlib or heavy import(s): {sorted(imported - allowed)}"
