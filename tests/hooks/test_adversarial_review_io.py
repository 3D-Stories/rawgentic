"""Tests for adversarial_review_lib artifact IO + safety (issue #77, Task 1).

Covers: path-traversal-safe resolution (final realpath MUST be under
project_root), size-cap truncation, and secret scanning before egress.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402


# --- resolve_artifact_path: traversal defense ---

def test_resolve_accepts_path_under_root(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    art = root / "docs" / "design.md"
    art.parent.mkdir(parents=True)
    art.write_text("# Design")
    resolved = arl.resolve_artifact_path(str(art), str(root))
    assert resolved == str(art.resolve())


def test_resolve_rejects_parent_traversal(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    with pytest.raises(arl.ArtifactError):
        arl.resolve_artifact_path(str(root / ".." / "secret.txt"), str(root))


def test_resolve_rejects_absolute_escape(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(arl.ArtifactError):
        arl.resolve_artifact_path("/etc/passwd", str(root))


def test_resolve_rejects_nul_byte(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(arl.ArtifactError):
        arl.resolve_artifact_path(str(root / "a\x00b.md"), str(root))


def test_resolve_rejects_sibling_prefix_escape(tmp_path):
    # root=/x/proj must NOT accept /x/proj-evil (startswith string trap)
    root = tmp_path / "proj"
    root.mkdir()
    sibling = tmp_path / "proj-evil"
    sibling.mkdir()
    art = sibling / "x.md"
    art.write_text("x")
    with pytest.raises(arl.ArtifactError):
        arl.resolve_artifact_path(str(art), str(root))


# --- read_artifact: size cap ---

def test_read_artifact_small_not_truncated(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    art = root / "a.md"
    art.write_text("hello world")
    text, truncated = arl.read_artifact(str(art), str(root))
    assert text == "hello world"
    assert truncated is False


def test_read_artifact_truncates_over_cap(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    art = root / "big.md"
    art.write_text("x" * 5000)
    # shrink the cap for the test via the module function honoring an explicit arg
    text, truncated = arl.read_artifact(str(art), str(root), max_bytes=1000)
    assert truncated is True
    assert len(text.encode("utf-8")) <= 1000


def test_read_artifact_missing_raises(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(arl.ArtifactError):
        arl.read_artifact(str(root / "nope.md"), str(root))


# --- scan_for_secrets ---

@pytest.mark.parametrize("text,expected_substr", [
    ("API_KEY=sk-abc123def456", "api key"),
    ("aws_secret_access_key = AKIAIOSFODNN7EXAMPLE", "aws"),
    ("password: hunter2hunter2", "password"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
    ("Authorization: Bearer eyJhbGciOiJI.payload.sig", "token"),
])
def test_scan_detects_secret_categories(text, expected_substr):
    hits = arl.scan_for_secrets(text)
    joined = " ".join(hits).lower()
    assert expected_substr in joined, f"{expected_substr!r} not in {hits!r}"


def test_scan_clean_text_returns_empty():
    assert arl.scan_for_secrets("# Design\nThis is a normal design document.") == []


def test_scan_dedupes_categories():
    text = "API_KEY=one\nAPI_KEY=two\nAPI_KEY=three"
    hits = arl.scan_for_secrets(text)
    # same category should appear once
    assert len(hits) == len(set(hits))
