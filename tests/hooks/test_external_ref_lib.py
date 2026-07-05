"""#194 — hooks/external_ref_lib.py: reliable use of external skills/commands.

A capability probe (verify a skill/command/plugin exists before a gate relies on
it — the #162 trap) + durable-copy-with-refresh (a gitignored local copy of a
vendored external command, refreshed on source-hash change, alerting if the
source vanishes) + a trust-gate (only vendor from known marketplaces, since an
external command file is third-party prompt content).
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import external_ref_lib as erl  # noqa: E402


def _make_cache(root: Path, marketplace="rawgentic", plugin="rawgentic",
                version="3.7.0", kind="commands", name="code-review.md", body="cmd"):
    d = root / marketplace / plugin / version / kind
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if name.endswith(".md"):
        p.write_text(body)
    else:  # a skill is a directory with a SKILL.md
        p.mkdir(exist_ok=True)
        (p / "SKILL.md").write_text(body)
    return p


class TestProbe:
    def test_finds_existing_command(self, tmp_path):
        _make_cache(tmp_path)
        r = erl.probe("command", "code-review", cache_root=tmp_path)
        assert r["exists"] is True
        assert r["marketplace"] == "rawgentic"
        assert r["trusted"] is True
        assert r["path"] and Path(r["path"]).exists()

    def test_missing_is_visible_skip(self, tmp_path):
        _make_cache(tmp_path)
        r = erl.probe("command", "does-not-exist", cache_root=tmp_path)
        assert r["exists"] is False
        assert r["reason"]  # a non-empty human reason for the visible skip

    def test_untrusted_marketplace_flagged(self, tmp_path):
        _make_cache(tmp_path, marketplace="temp_git_junk")
        r = erl.probe("command", "code-review", cache_root=tmp_path)
        assert r["exists"] is True
        assert r["trusted"] is False  # exists, but not from a trusted source

    def test_skill_kind_is_a_directory(self, tmp_path):
        _make_cache(tmp_path, kind="skills", name="implement-feature")
        r = erl.probe("skill", "implement-feature", cache_root=tmp_path)
        assert r["exists"] is True


class TestVendorCopy:
    def _src(self, tmp_path, body="v1"):
        src = _make_cache(tmp_path / "cache", name="code-review.md", body=body)
        return src

    def test_copies_and_writes_manifest(self, tmp_path):
        src = self._src(tmp_path)
        state = tmp_path / "vendored"
        r = erl.vendor_copy(str(src), "code-review", str(state), marketplace="rawgentic")
        assert r["status"] == "copied"
        copied = state / "code-review.md"
        assert copied.exists() and copied.read_text() == "v1"
        manifest = json.loads((state / "manifest.json").read_text())
        assert manifest["code-review"]["sha"] == hashlib.sha256(b"v1").hexdigest()

    def test_noop_when_unchanged(self, tmp_path):
        src = self._src(tmp_path)
        state = tmp_path / "vendored"
        erl.vendor_copy(str(src), "code-review", str(state), marketplace="rawgentic")
        r = erl.vendor_copy(str(src), "code-review", str(state), marketplace="rawgentic")
        assert r["status"] == "unchanged"

    def test_refresh_on_source_change(self, tmp_path):
        src = self._src(tmp_path, body="v1")
        state = tmp_path / "vendored"
        erl.vendor_copy(str(src), "code-review", str(state), marketplace="rawgentic")
        src.write_text("v2")
        r = erl.vendor_copy(str(src), "code-review", str(state), marketplace="rawgentic")
        assert r["status"] == "refreshed"
        assert (state / "code-review.md").read_text() == "v2"

    def test_vanished_source_alerts_and_keeps_copy(self, tmp_path):
        src = self._src(tmp_path)
        state = tmp_path / "vendored"
        erl.vendor_copy(str(src), "code-review", str(state), marketplace="rawgentic")
        src.unlink()
        r = erl.vendor_copy(str(src), "code-review", str(state), marketplace="rawgentic")
        assert r["status"] == "vanished"
        # the stale copy is retained (better than nothing)
        assert (state / "code-review.md").exists()

    def test_untrusted_marketplace_refused(self, tmp_path):
        src = self._src(tmp_path)
        state = tmp_path / "vendored"
        with pytest.raises(erl.UntrustedSourceError):
            erl.vendor_copy(str(src), "code-review", str(state),
                            marketplace="temp_git_junk")
        assert not (state / "code-review.md").exists()  # nothing vendored


class TestTrustGate:
    def test_known_marketplaces_trusted(self):
        assert erl.is_trusted("rawgentic")
        assert erl.is_trusted("claude-plugins-official")

    def test_junk_marketplace_untrusted(self):
        assert not erl.is_trusted("temp_git_junk")

    def test_env_extends_trust(self, monkeypatch):
        monkeypatch.setenv("RAWGENTIC_TRUSTED_MARKETPLACES", "my-private-mp, another")
        assert erl.is_trusted("my-private-mp")
        assert erl.is_trusted("another")
