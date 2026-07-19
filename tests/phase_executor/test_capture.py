"""Task 3: capture.py — atomic writes, .incomplete lifecycle, path-traversal defense, hashing."""
import os

import pytest

from phase_executor import capture
from phase_executor.capture import Capture, create_capture, hash_context, hash_text, sanitize_component


def test_atomic_write_no_leftover_tmp(tmp_path):
    p = tmp_path / "sub" / "f.txt"
    capture.atomic_write_text(p, "hello")
    assert p.read_text() == "hello"
    assert not list(tmp_path.rglob(".tmp-*")), "atomic write left a temp file behind"


def test_create_capture_writes_incomplete_first(tmp_path):
    cap = create_capture(tmp_path, "run1", "review", "att1")
    assert cap.incomplete
    assert (cap.path / capture.INCOMPLETE).exists()
    assert cap.path == tmp_path.resolve() / "run1" / "review" / "att1"


def test_create_capture_refuses_reuse(tmp_path):
    create_capture(tmp_path, "run1", "review", "att1")
    with pytest.raises(FileExistsError):
        create_capture(tmp_path, "run1", "review", "att1")


def test_finalize_clears_marker(tmp_path):
    cap = create_capture(tmp_path, "run1", "seat", "att1")
    cap.write_input("prompt")
    cap.write_observation({"ok": True})
    cap.finalize()
    assert not cap.incomplete
    assert (cap.path / "input.md").read_text() == "prompt"
    cap.finalize()  # idempotent


def test_capture_files_written(tmp_path):
    cap = create_capture(tmp_path, "r", "s", "a")
    cap.write_transport("raw-stdout")
    cap.write_output("clean output")
    cap.write_stderr("err trailer")
    assert (cap.path / "transport.stdout.txt").read_text() == "raw-stdout"
    assert (cap.path / "output.md").read_text() == "clean output"
    assert (cap.path / "stderr.txt").read_text() == "err trailer"


@pytest.mark.parametrize("bad", ["..", ".", "...", "   ", ""])
def test_sanitize_rejects_dot_and_empty(bad):
    with pytest.raises(ValueError):
        sanitize_component(bad)


@pytest.mark.parametrize("raw,expect", [
    ("../etc/passwd", ".._etc_passwd"),
    ("a/../b", "a_.._b"),
    ("claude-opus-4-8", "claude-opus-4-8"),
    ("seat name!", "seat_name_"),
])
def test_sanitize_maps_unsafe_chars(raw, expect):
    assert sanitize_component(raw) == expect


def test_create_capture_traversal_contained(tmp_path):
    # traversal components are sanitized to safe names; the dir stays under root
    cap = create_capture(tmp_path, "..", "..", "x") if False else create_capture(tmp_path, "run", "..bad..", "att")
    assert str(tmp_path.resolve()) in str(cap.path.resolve())


def test_hash_text_deterministic_and_prefixed():
    assert hash_text("abc") == hash_text("abc")
    assert hash_text("abc").startswith("sha256:")
    assert hash_text("abc") != hash_text("abd")


def test_hash_context_orders_and_empties():
    assert hash_context(None) == []
    assert hash_context(["a", "b"]) == [hash_text("a"), hash_text("b")]


def test_create_capture_dirs_are_0700(tmp_path):
    """#513: capture dirs hold transport.stdout.txt — the raw provider envelope
    including the session_id — so every dir create_capture creates gets 0700
    regardless of umask, matching the supervisor's specs/registry posture.
    A pre-existing caller-owned root keeps its mode (tmp_path here)."""
    old = os.umask(0o022)
    try:
        cap = create_capture(tmp_path, "run1", "review", "att1")
    finally:
        os.umask(old)
    for d in (cap.path, cap.path.parent, cap.path.parent.parent):
        assert (d.stat().st_mode & 0o777) == 0o700, f"{d} not 0700"


def test_create_capture_preexisting_dirs_not_rechmodded(tmp_path):
    """#513: only dirs this call CREATES get the chmod — a pre-existing
    intermediate (reuse across attempts) keeps its mode; posture is set at
    creation time."""
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    os.chmod(run_dir, 0o755)
    old = os.umask(0o022)
    try:
        cap = create_capture(tmp_path, "run1", "review", "att1")
    finally:
        os.umask(old)
    assert (cap.path.stat().st_mode & 0o777) == 0o700
    assert (cap.path.parent.stat().st_mode & 0o777) == 0o700  # review/ was created
    assert (run_dir.stat().st_mode & 0o777) == 0o755          # pre-existing untouched


def test_create_capture_creates_missing_root_0700(tmp_path):
    """#513 review F3: when the call itself creates the capture ROOT, the root
    is part of the private tree it made — 0700 like the rest, not default."""
    root = tmp_path / "caps"
    old = os.umask(0o022)
    try:
        create_capture(root, "run1", "review", "att1")
    finally:
        os.umask(old)
    assert (root.stat().st_mode & 0o777) == 0o700


def test_ensure_private_dir_shared_helper(tmp_path):
    """#513 review F1: the supervisor's timeout path creates the capture tree
    too — both creation sites share one posture helper. exist_ok semantics and
    created-dirs-only chmod pinned here."""
    from phase_executor.capture import ensure_private_dir
    pre = tmp_path / "existing"
    pre.mkdir()
    os.chmod(pre, 0o755)
    old = os.umask(0o022)
    try:
        target = ensure_private_dir(pre / "a" / "b")
        # idempotent re-call on an existing tree: no error, modes untouched
        ensure_private_dir(pre / "a" / "b")
    finally:
        os.umask(old)
    assert (target.stat().st_mode & 0o777) == 0o700
    assert ((pre / "a").stat().st_mode & 0o777) == 0o700
    assert (pre.stat().st_mode & 0o777) == 0o755  # pre-existing untouched
